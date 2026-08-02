# TEMPR Date-Range Filtering

## Contract

`TEMPRRetriever.retrieve(..., date_range=(start, end))` treats the requested
range as an inclusive eligibility boundary, not as one more ranking signal.
Both endpoints and document timestamps are normalized to UTC. A document with
a missing, malformed, or non-string `created_at` value is excluded because its
membership in an explicit range cannot be proven.

The same eligible document-ID set constrains semantic, BM25, temporal, and
graph retrieval before reciprocal-rank fusion. A final central filter protects
the fusion boundary as defense in depth. Without a `date_range`, documents
without timestamps retain their existing compatibility behavior.

This design keeps filtering deterministic and configurable per request while
avoiding a global time window that would silently change unrelated workloads.

## Operational Evidence

Long-lived vector-search user reports show why structured filters must define
the candidate set rather than merely affecting one ranking branch:

- Pinecone community support explains that metadata filters pre-filter vectors
  and that fallback semantic search requires a separate unfiltered query:
  [How does metadata filtering combine with embedding search?](https://community.pinecone.io/t/how-does-the-metadata-filtering-combines-with-embedding-search/552)
- Weaviate community support describes building an allow-list before vector
  search so only matching objects become candidates:
  [When does filtering occur?](https://forum.weaviate.io/t/when-does-filtering-occur/22267)
- A Weaviate date-filter report confirms that string-shaped or otherwise
  mistyped timestamps cannot be compared as dates and must be converted to a
  real date representation:
  [Date filters over Weaviate data](https://forum.weaviate.io/t/date-filters-over-weaviate-data/600)

Gludd therefore rejects unprovable timestamps only when an operator explicitly
requests a range and applies that decision before every retrieval strategy.

## Verification

The focused contract covers all strategies together, inclusive range
boundaries, UTC normalization, and missing/malformed/non-string timestamps.
The live integration regression pins the original failure: an old document
that scored highly in semantic and BM25 retrieval must not bypass the range.
