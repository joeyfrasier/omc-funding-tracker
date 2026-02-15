"""Zvec-powered semantic fuzzy matching for OFM reconciliation.

Uses all-MiniLM-L6-v2 (384-dim) embeddings via Zvec to match:
  1. Received payments (MoneyCorp payer_name) → remittance sources
  2. Remittance descriptions → pay run references
  3. Anomaly detection on payment patterns

Zero external dependencies beyond zvec + numpy (both already installed).
"""
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Lazy-load embedding model (takes ~1s first call, cached after)
_emb = None

def _get_embedder():
    global _emb
    if _emb is None:
        import zvec
        _emb = zvec.DefaultLocalDenseEmbedding()
        logger.info("Zvec embedding model loaded (dim=%d)", _emb.dimension)
    return _emb


def embed_text(text: str) -> np.ndarray:
    """Embed a single text string into a 384-dim vector."""
    return np.array(_get_embedder().embed(text))


def embed_batch(texts: List[str]) -> np.ndarray:
    """Embed multiple texts. Returns (N, 384) array."""
    emb = _get_embedder()
    return np.array([emb.embed(t) for t in texts])


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity between two sets of vectors. Returns (M, N) matrix."""
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return a_norm @ b_norm.T


# ── Data types ─────────────────────────────────────────────────────────

@dataclass
class FuzzyMatch:
    """A single fuzzy match result."""
    query: str
    candidate: str
    score: float
    candidate_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @property
    def confidence(self) -> str:
        if self.score >= 0.80:
            return "high"
        elif self.score >= 0.60:
            return "medium"
        elif self.score >= 0.45:
            return "low"
        return "none"


# ── Matching Engine ────────────────────────────────────────────────────

class VectorMatcher:
    """Semantic fuzzy matcher for OFM reconciliation data.
    
    Builds an in-memory vector index from candidate texts,
    then supports fast similarity search for matching.
    """

    def __init__(self):
        self.candidates: List[str] = []
        self.candidate_ids: List[Optional[str]] = []
        self.candidate_metadata: List[dict] = []
        self.vectors: Optional[np.ndarray] = None

    def add_candidates(self, texts: List[str], ids: Optional[List[str]] = None,
                       metadata: Optional[List[dict]] = None):
        """Add candidate texts to the index."""
        self.candidates.extend(texts)
        self.candidate_ids.extend(ids or [None] * len(texts))
        self.candidate_metadata.extend(metadata or [{}] * len(texts))
        # Rebuild vectors
        self.vectors = embed_batch(self.candidates)
        logger.info("Vector index built: %d candidates, dim=%d", len(self.candidates), self.vectors.shape[1])

    def search(self, query: str, top_k: int = 5, min_score: float = 0.3) -> List[FuzzyMatch]:
        """Find the best matches for a query string."""
        if self.vectors is None or len(self.candidates) == 0:
            return []

        q_vec = embed_text(query).reshape(1, -1)
        scores = cosine_similarity_matrix(q_vec, self.vectors)[0]

        # Get top-k indices sorted by score
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                break
            results.append(FuzzyMatch(
                query=query,
                candidate=self.candidates[idx],
                score=score,
                candidate_id=self.candidate_ids[idx],
                metadata=self.candidate_metadata[idx],
            ))
        return results

    def match_all(self, queries: List[str], top_k: int = 3,
                  min_score: float = 0.3) -> dict[str, List[FuzzyMatch]]:
        """Match multiple queries against the index. Returns {query: [matches]}."""
        if self.vectors is None or len(self.candidates) == 0:
            return {q: [] for q in queries}

        q_vecs = embed_batch(queries)
        sim_matrix = cosine_similarity_matrix(q_vecs, self.vectors)

        results = {}
        for i, query in enumerate(queries):
            scores = sim_matrix[i]
            top_indices = np.argsort(scores)[::-1][:top_k]
            matches = []
            for idx in top_indices:
                score = float(scores[idx])
                if score < min_score:
                    break
                matches.append(FuzzyMatch(
                    query=query,
                    candidate=self.candidates[idx],
                    score=score,
                    candidate_id=self.candidate_ids[idx],
                    metadata=self.candidate_metadata[idx],
                ))
            results[query] = matches
        return results


# ── OFM-Specific Functions ─────────────────────────────────────────────

RECON_DB_PATH = Path('data/recon.db')


def build_remittance_index() -> VectorMatcher:
    """Build a vector index from reconciliation records' remittance sources."""
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT remittance_source, remittance_email_id, remittance_date
        FROM reconciliation_records
        WHERE remittance_source IS NOT NULL AND remittance_source != ''
    """).fetchall()
    conn.close()

    matcher = VectorMatcher()
    if rows:
        texts = [r['remittance_source'] for r in rows]
        ids = [r['remittance_email_id'] for r in rows]
        metadata = [{'date': r['remittance_date']} for r in rows]
        matcher.add_candidates(texts, ids, metadata)
    return matcher


def build_payrun_index() -> VectorMatcher:
    """Build a vector index from cached pay run references."""
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, reference, tenant, total_amount, status
        FROM cached_payruns
        WHERE reference IS NOT NULL AND reference != ''
    """).fetchall()
    conn.close()

    matcher = VectorMatcher()
    if rows:
        # Combine reference + tenant for richer embeddings
        texts = [f"{r['reference']} {r['tenant'] or ''}".strip() for r in rows]
        ids = [str(r['id']) for r in rows]
        metadata = [{'tenant': r['tenant'], 'amount': r['total_amount'], 'status': r['status']} for r in rows]
        matcher.add_candidates(texts, ids, metadata)
    return matcher


def match_received_payments() -> List[dict]:
    """Match unmatched received payments against remittance sources using vector similarity.
    
    Returns list of suggested matches with scores.
    """
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get unmatched received payments
    unmatched = conn.execute("""
        SELECT id, payer_name, amount, payment_date, account_name
        FROM received_payments
        WHERE match_status = 'unmatched' AND payer_name IS NOT NULL
    """).fetchall()

    # Get remittance sources as candidates
    remittances = conn.execute("""
        SELECT DISTINCT remittance_source, remittance_email_id, 
               SUM(remittance_amount) as total_amount, remittance_date
        FROM reconciliation_records
        WHERE remittance_source IS NOT NULL AND remittance_source != ''
        GROUP BY remittance_source
    """).fetchall()
    conn.close()

    if not unmatched or not remittances:
        return []

    # Build index from remittance sources
    matcher = VectorMatcher()
    texts = [r['remittance_source'] for r in remittances]
    ids = [r['remittance_email_id'] for r in remittances]
    metadata = [{'total_amount': r['total_amount'], 'date': r['remittance_date']} for r in remittances]
    matcher.add_candidates(texts, ids, metadata)

    # Match each unmatched payment
    suggestions = []
    queries = [f"{p['payer_name']} {p['account_name'] or ''}".strip() for p in unmatched]
    all_matches = matcher.match_all(queries, top_k=3, min_score=0.35)

    for payment, query in zip(unmatched, queries):
        matches = all_matches[query]
        if matches:
            suggestions.append({
                'payment_id': payment['id'],
                'payer_name': payment['payer_name'],
                'payment_amount': payment['amount'],
                'payment_date': payment['payment_date'],
                'suggested_matches': [
                    {
                        'remittance_source': m.candidate,
                        'email_id': m.candidate_id,
                        'score': round(m.score, 3),
                        'confidence': m.confidence,
                        'remittance_amount': m.metadata.get('total_amount'),
                    }
                    for m in matches
                ],
            })

    return suggestions


def find_anomalous_payments(threshold: float = 2.0) -> List[dict]:
    """Detect anomalous payments using embedding distance from cluster centroid.
    
    Payments whose descriptions are semantically far from the centroid
    of their tenant's typical payments may be errors or misrouted.
    
    Args:
        threshold: Z-score threshold for flagging anomalies (default 2.0 = ~95th percentile)
    """
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT nvc_code, remittance_source, invoice_tenant, remittance_amount
        FROM reconciliation_records
        WHERE remittance_source IS NOT NULL AND invoice_tenant IS NOT NULL
    """).fetchall()
    conn.close()

    if len(rows) < 10:
        return []

    # Group by tenant
    tenant_groups: dict[str, list] = {}
    for r in rows:
        tenant = r['invoice_tenant']
        tenant_groups.setdefault(tenant, []).append(r)

    anomalies = []
    for tenant, records in tenant_groups.items():
        if len(records) < 5:
            continue

        texts = [r['remittance_source'] for r in records]
        vecs = embed_batch(texts)

        # Compute centroid and distances
        centroid = vecs.mean(axis=0)
        distances = np.array([1 - cosine_similarity(v, centroid) for v in vecs])

        # Z-score
        mean_dist = distances.mean()
        std_dist = distances.std() + 1e-10
        z_scores = (distances - mean_dist) / std_dist

        for i, z in enumerate(z_scores):
            if z > threshold:
                anomalies.append({
                    'nvc_code': records[i]['nvc_code'],
                    'tenant': tenant,
                    'remittance_source': records[i]['remittance_source'],
                    'amount': records[i]['remittance_amount'],
                    'z_score': round(float(z), 2),
                    'distance': round(float(distances[i]), 4),
                })

    return sorted(anomalies, key=lambda x: x['z_score'], reverse=True)


def find_potential_duplicates(min_score: float = 0.92) -> List[dict]:
    """Find potential duplicate reconciliation records using semantic similarity.
    
    Records with very similar descriptions + close amounts may be duplicates.
    """
    conn = sqlite3.connect(str(RECON_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT nvc_code, remittance_source, remittance_amount, invoice_tenant
        FROM reconciliation_records
        WHERE remittance_source IS NOT NULL
        ORDER BY remittance_source
    """).fetchall()
    conn.close()

    if len(rows) < 2:
        return []

    texts = [r['remittance_source'] for r in rows]
    vecs = embed_batch(texts)
    sim_matrix = cosine_similarity_matrix(vecs, vecs)

    duplicates = []
    seen = set()
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            score = float(sim_matrix[i][j])
            if score >= min_score:
                pair_key = (rows[i]['nvc_code'], rows[j]['nvc_code'])
                if pair_key not in seen:
                    seen.add(pair_key)
                    # Check amount proximity too
                    amt_a = rows[i]['remittance_amount'] or 0
                    amt_b = rows[j]['remittance_amount'] or 0
                    amt_diff = abs(amt_a - amt_b)
                    duplicates.append({
                        'nvc_a': rows[i]['nvc_code'],
                        'nvc_b': rows[j]['nvc_code'],
                        'source_a': rows[i]['remittance_source'],
                        'source_b': rows[j]['remittance_source'],
                        'amount_a': amt_a,
                        'amount_b': amt_b,
                        'amount_diff': round(amt_diff, 2),
                        'similarity': round(score, 3),
                    })

    return sorted(duplicates, key=lambda x: x['similarity'], reverse=True)


# ── CLI test ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    import json
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("OFM Vector Matcher — Zvec MVP")
    print("=" * 70)

    # Test 1: Basic similarity
    print("\n📐 Test 1: Semantic similarity on OFM-style texts")
    t0 = time.time()
    pairs = [
        ("OMC Feb W1 Contractors", "Omnicom February Week 1 - Contractor Payments"),
        ("OGI Shared Service Center Advertising LLC", "Omnicom Group Inc Shared Services Advertising"),
        ("BBDO New York January payroll", "DDB Chicago contractor payments February"),
        ("MoneyCorp wire transfer USD", "BBDO payroll run January"),
    ]
    for a, b in pairs:
        va, vb = embed_text(a), embed_text(b)
        sim = cosine_similarity(va, vb)
        marker = '🟢' if sim > 0.7 else '🟡' if sim > 0.5 else '⚪'
        print(f"  {marker} {sim:.3f}  \"{a}\" ↔ \"{b}\"")
    print(f"  ⏱️  {time.time() - t0:.2f}s")

    # Test 2: Match against live recon DB
    if RECON_DB_PATH.exists():
        print(f"\n📊 Test 2: Received payment matching (live DB)")
        t0 = time.time()
        suggestions = match_received_payments()
        print(f"  Found {len(suggestions)} unmatched payments with suggestions")
        for s in suggestions[:5]:
            print(f"  💰 {s['payer_name']} (${s['payment_amount']:.2f})")
            for m in s['suggested_matches'][:2]:
                print(f"     → {m['confidence']:6s} ({m['score']:.3f}) {m['remittance_source']}")
        print(f"  ⏱️  {time.time() - t0:.2f}s")

        print(f"\n🔍 Test 3: Anomaly detection")
        t0 = time.time()
        anomalies = find_anomalous_payments()
        print(f"  Found {len(anomalies)} anomalous payments")
        for a in anomalies[:5]:
            print(f"  ⚠️  {a['nvc_code']} ({a['tenant']}) z={a['z_score']:.1f} — {a['remittance_source']}")
        print(f"  ⏱️  {time.time() - t0:.2f}s")

        print(f"\n🔄 Test 4: Duplicate detection")
        t0 = time.time()
        dupes = find_potential_duplicates()
        print(f"  Found {len(dupes)} potential duplicate pairs")
        for d in dupes[:5]:
            print(f"  📋 {d['similarity']:.3f} — {d['nvc_a']} ↔ {d['nvc_b']} (diff=${d['amount_diff']:.2f})")
        print(f"  ⏱️  {time.time() - t0:.2f}s")
    else:
        print(f"\n⚠️  Recon DB not found at {RECON_DB_PATH} — skipping live tests")

    print("\n✅ Done!")
