from gpumon.rca.embeddings import HashingEmbedder, cosine
from gpumon.rca.rag import Document, EmbeddingRetriever, KnowledgeBase


def test_hashing_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(dim=128)
    a = emb.embed(["straggler stalling all-reduce collective"])[0]
    b = emb.embed(["straggler stalling all-reduce collective"])[0]
    assert a == b
    assert len(a) == 128
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6  # unit length
    # related text is closer than unrelated text
    rel = emb.embed(["a slow straggler rank stalls the all-reduce"])[0]
    unrel = emb.embed(["thermal throttling from a hot datacenter rack"])[0]
    assert cosine(a, rel) > cosine(a, unrel)


def test_embedding_retriever_ranks_semantically():
    r = EmbeddingRetriever(HashingEmbedder())
    r.add(Document("straggler", "runbook", "Straggler rank", "one rank stalls every all-reduce collective barrier"))
    r.add(Document("thermal", "runbook", "Thermal throttling", "gpus overheat and clock throttle on a hot node"))
    r.add(Document("oom", "runbook", "HBM OOM", "gpu memory saturated out of memory"))
    hits = r.search("a slow rank is stalling the all-reduce", k=2, kind="runbook")
    assert hits and hits[0][0].doc_id == "straggler"


def test_knowledge_base_accepts_embedding_retriever():
    kb = KnowledgeBase(retriever=EmbeddingRetriever(HashingEmbedder()))
    runbooks, incidents = kb.retrieve("one rank straggler stalling all-reduce", k=3)
    assert runbooks and runbooks[0][0].doc_id == "straggler"
    assert incidents  # seed incidents indexed too
