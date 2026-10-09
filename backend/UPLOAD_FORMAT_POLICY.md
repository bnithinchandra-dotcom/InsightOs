# Upload format policy

The dataset file upload endpoint accepts `.csv`, `.tsv`, `.xlsx`, `.json`,
`.parquet`, and `.xml`. Other extensions, including legacy `.xls` workbooks and
`.zip` packages, are rejected.

For these extensions, the endpoint accepts the corresponding MIME types:

| Extension | Accepted supplied MIME types |
| --- | --- |
| `.csv` | `text/csv`, `application/csv`, `text/plain` |
| `.tsv` | `text/tab-separated-values`, `text/tsv`, `text/plain` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.json` | `application/json`, `text/json` |
| `.parquet` | `application/vnd.apache.parquet`, `application/x-parquet` |
| `.xml` | `application/xml`, `text/xml` |

An omitted MIME type or `application/octet-stream` is also accepted for any of
the listed extensions. Extension and MIME checks validate the upload labels;
the parser independently inspects content and rejects malformed or mismatched
files.

Parsing is synchronous during upload. On success, the original bytes are stored
unchanged and the file record contains detected format, column names and
physical types, row count, and format-specific metadata. The dataset returns to
the existing `Uploaded` state. On a parse failure no new object/file record is
created and the dataset is marked `Failed`; the response includes a safe error
code and message. `MAX_DATASET_SIZE_MB` limits upload size;
`MAX_DATASET_ROWS`, `MAX_DATASET_COLUMNS`, `MAX_XLSX_UNCOMPRESSED_MB`,
`MAX_XLSX_ENTRIES`, `MAX_PARQUET_ROW_GROUPS`, `MAX_XML_SIZE_MB`,
`MAX_XML_DEPTH`, `MAX_XML_ELEMENTS`, and `MAX_JSON_DEPTH` configure parser
resource limits.

CSV and TSV use UTF-8 (an optional UTF-8 BOM is accepted), with the first record
as the header and strict row widths. Their physical type is text; values are
not coerced. A well-formed XML document or recognized binary signature is
rejected as mismatched delimited content; ordinary single-column text remains
valid. JSON supports an array of objects, a flat object with scalar values, or
a wrapper object whose only property is an array of record objects. Sibling
fields in wrapper objects are rejected rather than discarded. Nested JSON
values inside records remain object/array values. XLSX parses each worksheet
using its first non-empty row as the header; formulas are identified but not
evaluated. Parquet schema types come from its Arrow schema, and the full data
stream is scanned to verify rows.

XML support is intentionally limited to a well-formed document with exactly
one unambiguous group of repeated same-name sibling record elements. The record
container cannot mix unrelated element siblings. Namespace names use expanded
Clark notation (`{uri}local`); nested leaf names are joined with `/`, and
attributes use `/@name`. Attributes are preserved, missing elements and empty
values are counted separately, and mixed text/element content or repeated
nested fields are rejected as unsupported rather than silently flattened.
All DTD declarations, including internal subsets, and external entities are
forbidden. XML is parsed with a bounded tree builder that enforces depth and
element-count limits as start elements arrive, before those nodes are added to
the tree. The configured XML file-size limit is also applied before parsing.

These results describe file structure and physical types only. Parsing does
not profile distributions, infer semantic meaning, modify source values, or
perform preprocessing. An uploaded object remains the source of truth.

Database sources such as PostgreSQL and MySQL, live servers, streaming, polling,
and cloud object storage require separate connector-based ingestion paths.
They are not file formats and are not handled by this endpoint.
