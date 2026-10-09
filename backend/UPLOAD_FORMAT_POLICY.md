# Upload format policy

The dataset file upload endpoint currently accepts these file extensions for
storage: `.csv`, `.tsv`, `.xlsx`, `.json`, and `.parquet`. Other extensions,
including legacy `.xls` workbooks and `.zip` packages, are rejected.

For these extensions, the endpoint accepts the corresponding MIME types:

| Extension | Accepted supplied MIME types |
| --- | --- |
| `.csv` | `text/csv`, `application/csv`, `text/plain` |
| `.tsv` | `text/tab-separated-values`, `text/tsv`, `text/plain` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.json` | `application/json`, `text/json` |
| `.parquet` | `application/vnd.apache.parquet`, `application/x-parquet` |

An omitted MIME type or `application/octet-stream` is also accepted for any of
the listed extensions. Extension and MIME checks are upload-label validation
only; they do not inspect or prove the file contents.

The API stores the uploaded bytes and records metadata. It does not parse these
formats. The current backend dependencies do not include format parsers, and
the worker currently only establishes Redis connectivity; consequently,
successful storage is not evidence that a file has been successfully parsed.
Profiling and downstream analytics support are not implemented or established
for these formats by the upload endpoint.

Database sources such as PostgreSQL and MySQL, and cloud object storage such as
S3, require separate connector-based ingestion paths. They are not file formats
and are not handled by this endpoint.
