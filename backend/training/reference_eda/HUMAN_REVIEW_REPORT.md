# Human review disposition — Reference EDA Qwen3 corpus

**Review inputs:** `reference_eda_reviewed.zip`; subsequent Reference EDA Qwen3 Corpus v1 review
**Review date:** 2026-11-03
**Disposition:** `CHANGES_REQUIRED — NOT APPROVED FOR FINE-TUNING`
**Approval note:** This implementation log and AI-assisted technical review are not qualified human domain-expert sign-off. Individual records require authorized, record-level review.

## Human review workflow implementation

The package now provides a CLI-based review lifecycle for the 250 training
examples and 20 evaluation cases. It exports separate category queues, supports
individual inspection, validates explicit decisions and corrections, appends
reasoned review history, reports progress by category/scenario/split, and builds
approved-only artifacts without modifying source records.

Review events are stored separately in `review_history.jsonl`. Each event
captures record category/ID, scenario, reviewer identifier, decision,
timezone-aware timestamp, comments, issues, requested corrections, source and
content hashes, revision number, previous decision, reason for a changed
decision, and any corrected content/reference. Source changes invalidate a
previous decision by hash; a corrected record must receive a separate subsequent
review before approval. Dataset regeneration resets legacy embedded statuses to
draft and does not erase the separate review history.

The fine-tuning preflight now requires a complete approved-only snapshot whose
approval manifest and file contents match the current source records and review
history. A partial review, a stray review file, or edited approval metadata does
not make the training gate ready. The current corpus remains entirely draft;
this implementation did not approve any record or run fine-tuning.

## Reconciliation with the subsequent review

The subsequent report describes an earlier v1 corpus whose domains were campaign,
commerce, subscription, inventory, and support. The current worktree is the later
v2 draft corpus: marketing, predictive maintenance, e-commerce, fraud, and
healthcare readmission. Findings that apply to both generations have been retained
or strengthened below; v1-only fixture details are not represented as current v2
facts. The current 250 records remain draft, and no training or fine-tuning is
authorized.

## 1. Training examples — changes required

The supplied ZIP identified missing predictive-maintenance, fraud, and
healthcare-readmission scenarios; repetitive single-turn templates; ungrounded
tool identifiers; incomplete serialized tool results; invalid inspection-handoff
mock data; and generic code that did not calculate the named KPI.

The current blueprint now covers marketing, predictive maintenance, e-commerce,
fraud, and healthcare readmission. Examples are stakeholder-led multi-turn
conversations, tool identifiers are stated in the preceding user context, tool
responses use the full `ToolResult` field set, and inspection handoffs are
validated against the application contract. SQL and Python KPI snippets execute
against the synthetic fixtures; DAX examples are checked for required fields and
measures.

**Record decision:** all 250 examples remain `draft`. The changes do not constitute
individual content approval or authorize training.

## 2. Train, validation, and held-out splits — structure approved

The 200/25/25 structure is retained. The 50 topic groups stay within one split,
all five industries occur in every split, and automated checks find no split
leakage. The approval is structural only; validation and held-out record review
is still pending.

## 3. Evaluation suite — scaffold accepted and expanded

The suite retains 20 non-tool held-out cases and now includes scenario profiles,
mandatory facts, incorrect behaviors, and a 100-point weighted rubric. It
includes the five required stakeholder cases for marketing budget allocation,
maintenance risk with incomplete sensors, an e-commerce revenue discrepancy,
fraud recall versus false positives, and clinical readmission-risk review.

Every case remains `draft`; a workflow-aware runner is still required to evaluate
tool execution.

## 4. Synthetic fixture calculations — verified with conditions

The current fixture tests verify, among other results:

- Marketing: Google has 36 qualified leads, qualified CPA 16.67, and attributed
  ROAS 3.00; Facebook has 100 leads but 20 qualified leads and ROAS 1.80.
- Marketing attribution model and window are declared assumptions, but aggregate
  rows contain no touch-level evidence to audit attribution independently.
  Impressions are absent, so CTR cannot be calculated. A zero-spend fixture row
  exercises undefined CPA and ROAS denominators.
- E-commerce: net line revenue 1,260.00, gross profit 640.00, and gross margin
  50.79% under the fixture's explicit discount, refund, cancellation, and COGS
  rules. This is fixture net line sales, not complete accounting revenue; tax
  and shipping are absent. A fully refunded product group exercises undefined
  zero-revenue margin.
- Maintenance: one observation has incomplete sensor coverage; the target is
  confirmed failure strictly after the prediction timestamp and within the
  following 30 days. Repeated positive observation rows are not unique
  machine-failure-event counts.
- Fraud: reviewed-label precision and recall are both 66.67% at the documented
  threshold; a flag is not a confirmed fraud label.
- Healthcare: four eligible episodes have complete 30-day follow-up, two have an
  unplanned readmission, and the descriptive rate is 50.00%. One eligible row
  has a feature-availability leakage concern and is called out separately.

These are deterministic synthetic fixture calculations, not production
measurements, causal findings, validated clinical predictions, or model
performance claims. Healthcare rates use eligible index discharges with complete
30-day follow-up as the denominator; planned readmissions are excluded from the
unplanned outcome numerator. The topic definitions state the required caveats.

## Evaluation suite and remaining work

The 20 held-out text cases remain a human-review smoke-test scaffold, not an
approved model-comparison benchmark. They now include scenario-specific
acceptance criteria, rejection criteria, and case-level 100-point rubrics in
addition to the shared weighted dimensions. All cases remain draft. Tool-call
examples are still excluded from this text-only suite; a workflow-aware tool
evaluation runner and record-level qualified human review remain outstanding.

## 5. Claim validation — fail-closed policy retained

The production claim validator remains unchanged and continues to fail closed for
claim categories without evidence-specific resolvers. Current advanced-claim
coverage is incomplete. Do not enable metric, comparative, statistical, forecast,
prediction, anomaly, causal, or preprocessing claims until the production path
can resolve the corresponding current, verified, in-scope persisted evidence.
The review report does not authorize or require production claim-validation
changes.

## Validation evidence

The generated dataset report records the split counts, scenario coverage,
registered tool-call counts, validation results, and output hashes. Focused
tests additionally check tool argument schemas, complete `ToolResult` shapes,
inspection-handoff contracts, fixture KPI calculations, evaluation structure,
and draft-only review metadata.
