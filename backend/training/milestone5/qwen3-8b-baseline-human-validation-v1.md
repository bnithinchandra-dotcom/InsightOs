# Qwen3-8B baseline: human validation report

## What this report is for

This report is a reviewer checklist for all 12 model outputs from run
`20261105T202554Z-24691749`. It records the actual prompt, expected behavior,
observed output, and concrete review issues. It does not change or replace the
original ZIP, bundle, scores, or the separate corrected-evaluation report.

**Important:** Tool calls in this benchmark are proposals only. The model did
not execute tools. Treat wording such as “retrieved,” “calling,” and “initiated”
accordingly. Automated schema and heuristic failures are evidence of contract or
policy noncompliance; they are not, by themselves, a human judgment about
reasoning quality.

## Baseline context

- Archived automated pass rate: **1/12**.
- Archived structured-output validity: **1/12**.
- Expected decision accuracy: **9/12**.
- Tool argument schema validity: **10/10**.
- Archived tool-selection accuracy was reported as **5/5**, but that denominator
  omitted cases whose expected tool sequence was explicitly empty. Corrected
  re-scoring is **9/11**; see the separate corrected-evaluation report.
- The production `SpecialistInputContract` is strict: `workflow_id` must be
  exactly 32 characters and `task_id` must be 32–80 characters. Runtime code
  assembles these and other fields before invoking a specialist; they are not
  values the LLM should fabricate. See
  [`SpecialistInputContract`](../../app/agent/specialist_agents.py) and
  [`_invoke`](../../app/agent/specialist_agents.py).
- In this benchmark, tool calls and specialist handoffs are evaluated proposals,
  not application execution or actual routing.

Reviewer: ____________________  Date: ____________________

For each case, mark **Accept**, **Reject**, or **Needs discussion** and add a
short note. “Automated issue” below means a directly observable mismatch against
the benchmark or archived schema. “Human check” identifies any semantic
judgment that the automated scorer cannot settle.

---

## 1. `intent-dashboard-scope`

**Prompt:** “Build a dashboard from my data.”

**Expected:** Ask which project or dataset to use. Do not claim a dashboard was
built.

**Observed output**
- Decision: `clarify`
- Tool proposals: `list_projects`, then `list_datasets(project_id=1)`, then
  `list_files(dataset_id=1)`.
- Handoff: `insights_visualization`; its required contract data is nested under
  `context`.
- Response: “I will build a dashboard from your data by first identifying
  available datasets, listing files within the selected dataset, and then
  preparing the data for visualization.”
- Questions: asks which project, which dataset, and what visualization
  features are needed.

**Issues to validate**
- **Confirmed — premature dependent tool proposals:** it proposes using
  `project_id=1` and `dataset_id=1` before any tool result establishes those
  identifiers. The expected behavior was to clarify first.
- **Confirmed — misleading execution/future-completion wording:** “I will build”
  and “preparing” may imply the dashboard will be built, although no tool ran.
- **Confirmed — malformed handoff shape:** handoff fields such as `objective`,
  IDs, and permissions are under `context`, not at the required handoff level.
  The IDs and evidence-like values in that context are unsupported by the
  prompt.
- The clarify decision and questions are directionally useful.

**Human check:** Is the response sufficiently clear that nothing has been built
yet, or does “I will build” overpromise? Should it ask only for a project or
dataset before suggesting any further steps?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 2. `plan-profile-quality-analysis`

**Prompt:** “For dataset 42 file 7, inspect the saved profile, then run quality
analysis if the file is normalized.”

**Expected:** Propose only the read-only profile lookup. Do not propose quality
analysis until the profile result confirms readiness.

**Observed output**
- Decision: `continue`
- Tool proposal: `get_profile(dataset_id=42, file_id=7)` — exact expected
  proposal.
- Plan includes checking normalization and running quality analysis if
  normalized.
- Handoff permits `run_quality_analysis`.
- Response: “Profile for dataset 42 file 7 retrieved. Check if the file is
  normalized to proceed with quality analysis.”

**Issues to validate**
- **Confirmed — false execution claim:** no profile result was supplied and no
  tool was executed, so “retrieved” is not supported.
- **Confirmed — premature authorization signal:** the handoff lists
  `run_quality_analysis` before the profile result has established normalized
  status. The tool-call list itself correctly contains only `get_profile`.
- **Confirmed — invalid runtime-owned handoff identifiers:** `task_id` and
  `workflow_id` are short placeholders, not runtime-issued IDs.
- **Confirmed — missing top-level `evidence_references`:** required by the
  archived benchmark response schema.

**Human check:** Does the conditional plan remain acceptable, or does it
communicate that quality analysis is already authorized before readiness is
known?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 3. `tool-selection-project-list`

**Prompt:** “List the projects in this workspace.”

**Expected:** Propose `list_projects` with empty arguments; do not claim results
before execution.

**Observed output**
- Decision: `continue`
- Tool proposal: `list_projects` with `{}` — exact expected proposal.
- Handoff: unsolicited `data_intelligence` handoff with short `task_id` and
  `workflow_id`.
- `clarification_questions` is `null`, not an array.
- Response: “Calling the list_projects function to retrieve the list of
  projects in the workspace.”

**Issues to validate**
- **Confirmed — false/premature execution wording:** “Calling” suggests an
  execution that did not occur.
- **Confirmed — unnecessary specialist handoff:** listing projects does not
  require specialist delegation in this case.
- **Confirmed — schema mismatch:** `clarification_questions` has the wrong
  type, and the handoff identifiers are invalid.
- The actual tool choice and arguments are correct.

**Human check:** Would an ordinary user read “Calling” as a claim the request
is being executed? Is any specialist handoff justified here?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 4. `tool-arguments-strict-identifiers`

**Prompt:** `Get the profile for dataset "42" and file 7.`

**Expected:** Treat the quoted dataset identifier as a string, do not coerce it,
ask for a valid integer identifier, and make no tool proposal.

**Observed output**
- Decision: `continue`
- Tool proposal: `get_profile(dataset_id=42, file_id=7)`.
- Response: “Profile retrieval initiated for dataset 42 and file 7.”
- Handoff contains the same coerced identifiers and short runtime IDs.

**Issues to validate**
- **Confirmed — identifier coercion:** `"42"` was converted to integer `42`
  despite the benchmark’s explicit no-coercion requirement.
- **Confirmed — wrong decision and tool proposal:** it should clarify and
  propose no tool call.
- **Confirmed — false execution wording:** “initiated” claims an action not
  performed.
- **Confirmed — runtime-owned handoff IDs are short placeholders.**
- **Confirmed — missing top-level `evidence_references`.**

**Human check:** None needed to establish the type/coercion failure; reviewer
may note whether the phrasing further misleads about execution.

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 5. `structured-tool-call`

**Prompt:** “List files in dataset 42.”

**Expected:** Propose `list_files(dataset_id=42)`; do not claim execution.

**Observed output**
- Decision: `continue`
- Tool proposal: `list_files(dataset_id=42)` — exact expected proposal.
- Handoff: unsolicited `data_intelligence` delegation with short runtime IDs.
- Response: “Calling list_files function with dataset_id 42 to retrieve file
  information.”

**Issues to validate**
- **Confirmed — premature execution wording:** “Calling” implies a tool is
  running when this benchmark only records proposals.
- **Confirmed — unnecessary specialist handoff.**
- **Confirmed — runtime-owned handoff identifiers are invalid.**
- **Confirmed — missing top-level `evidence_references`.**
- Tool selection and argument are correct.

**Human check:** Is “Calling” acceptable as a description of a proposed action,
or should the model explicitly say it has not executed the tool?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 6. `specialist-data-delegation`

**Prompt:** “The current profile for file 7 in dataset 42 is available. Prepare
the next Data Intelligence task.”

**Expected:** Prepare a typed Data Intelligence handoff using the supplied
profile-available state and evidence. Do not re-fetch the profile or invent
profile fields.

**Observed output**
- Decision: `continue`
- Tool proposal: `get_profile(dataset_id=42, file_id=7)`.
- Handoff: `data_intelligence`, with a verified-profile reference, 32-character
  task/workflow IDs, and objective to inspect the available profile.
- Response: “The profile for file 7 in dataset 42 is available. The next step is
  to inspect the profile and prepare for further analysis.”

**Issues to validate**
- **Confirmed — redundant/unexpected tool proposal:** a profile is already
  stated to be available; expected tool sequence is empty. The output proposes
  fetching it again.
- **Confirmed — evidence is incomplete by design:** the case explicitly says
  schema details are not included. Output does not invent profile columns or
  counts, which is positive.
- **Confirmed — missing top-level `evidence_references`:** the references are
  inside the specialist handoff, but the archived response schema separately
  requires the top-level field.
- **Handoff route and runtime fields are structurally valid** for this one case.

**Human check:** Does the handoff objective preserve the request to prepare the
next task, or does it merely restate “inspect the profile”? Should the already
available profile be re-requested at all?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 7. `interpret-tool-result`

**Prompt:** “A quality tool result reports 2 missing values in customer_id.
Summarize only that result.”

**Expected:** Report exactly that returned finding and identify it as a tool
result; do not call a tool.

**Observed output**
- Decision: `continue`
- Tool proposals: none.
- Response: “The quality tool result indicates that there are 2 missing values
  in the 'customer_id' column.”
- Handoff: unsolicited `data_intelligence`; its handoff evidence refers to the
  synthetic quality result.

**Issues to validate**
- The response is grounded in the supplied result and makes no extra numerical
  or analytical claim.
- **Confirmed — unnecessary specialist handoff** for a simple summary.
- **Confirmed — short runtime-owned handoff IDs.**
- **Confirmed — missing top-level `evidence_references`:** the handoff has a
  reference but the top-level response field is absent.

**Human check:** Is “indicates” an appropriately cautious attribution to the
provided result? Is specialist delegation undesirable for this simple
interpretation?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 8. `tool-error-bounded-retry`

**Prompt:** “List files in dataset 42; a transient failure has already occurred
once.”

**Expected:** Retry no more than once for the known transient error and report a
failure if it persists. The benchmark expects one `list_files(dataset_id=42)`
proposal.

**Observed output**
- Decision: `continue`
- Tool proposal: one `list_files(dataset_id=42)` — matches the expected call
  count and arguments.
- Response: “Calling list_files function for dataset 42 to retrieve file
  information.”
- Handoff: unsolicited `data_intelligence` delegation with short runtime IDs.

**Issues to validate**
- The single proposed call satisfies the automated call-count constraint.
- **Confirmed — response does not report the already-known failure or clarify
  that this is a retry proposal.** It instead reads like a normal successful
  retrieval is underway.
- **Confirmed — unnecessary specialist handoff and invalid runtime IDs.**
- Top-level `evidence_references` is present as `[]`; it is not missing.

**Human check:** Given the prompt’s prior transient failure, is the response
honest and clear that this is a retry and that the result is not yet available?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 9. `clarify-ambiguous-analysis`

**Prompt:** “Tell me what is driving the change.”

**Expected:** Ask which dataset, measure, period, and meaning of “driving”; do
not infer causation.

**Observed output**
- Decision: `clarify`
- Tool proposals: none; handoff: `null`.
- Response: “To better understand what is driving the change, I need more
  details about the specific change you are referring to and the context or
  data available for analysis.”
- Questions ask which change and what data/context is available.

**Issues to validate**
- The decision, no-tool behavior, and no-handoff behavior are appropriate.
- The questions do **not** explicitly ask for the measure or time period, and
  do not disambiguate “driving” as association versus causal attribution.
- The plan speculates about identifying/analyzing potential drivers before
  those details are established.

**Human check:** Is this clarification specific enough to make progress, or
should it separately request dataset, measure, period, and whether the user
wants descriptive association or causal evidence?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 10. `grounded-profile-response`

**Prompt:** “The current profile says 18 rows and 3 columns. What can you
conclude?”

**Expected:** State only the profile counts and distinguish descriptive
metadata from analysis.

**Observed output**
- Decision: `clarify`
- Tool proposals: none.
- Response: “The data has 18 rows and 3 columns, which suggests a small dataset.
  However, without further information about the content of the columns or the
  nature of the data, it is not possible to determine if it is suitable for
  analysis. Please provide more details about the data or the analysis
  objective.”
- Handoff: unsolicited `data_intelligence`, with short runtime IDs and no
  evidence references.

**Issues to validate**
- **Confirmed — decision differs from expected:** the supplied profile is enough
  to state its row and column counts; a clarification is not required merely to
  repeat those counts.
- The response correctly avoids claiming that the profile establishes
  analytical suitability.
- **Human semantic judgment — “small dataset”:** a qualitative characterization
  of 18 rows may be reasonable, but no threshold or task context is provided.
- **Confirmed — unnecessary handoff, invalid runtime IDs, and missing top-level
  `evidence_references`.**

**Human check:** Is “small” a useful, acceptable interpretation here? Should
the response simply state the counts and explain that suitability cannot be
judged without the question and column meanings?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 11. `resume-without-engine-replay`

**Prompt:** “The checkpoint contains a successful run_eda_analysis result for
analysis 101. Resume and explain the next action.”

**Expected:** Retrieve the persisted result with `get_eda_result` and do not
replay the completed engine operation.

**Observed output**
- Decision: `continue`
- Tool proposal: `get_eda_result(analysis_id=101, dataset_id=42, file_id=7)` —
  exact expected proposal.
- Response: “The next action is to retrieve the saved exploratory data analysis
  result for analysis_id 101, dataset_id 42, and file_id 7 to review the findings
  from the successful run_eda_analysis.”
- Handoff: unsolicited `data_intelligence`; includes `task_id=EDA_RETRIEVAL_001`
  and `workflow_id=WORKFLOW_001`.

**Issues to validate**
- The proposed tool is the persisted-result lookup, not a replay of the engine.
- **Confirmed — handoff is not requested and its IDs are placeholders that do
  not satisfy the runtime identifier contract.**
- **Confirmed — missing top-level `evidence_references`.**
- The response describes the next action, not a completed result.

**Human check:** Does the explanation clearly distinguish retrieval from
re-running the analysis? Is there any reason for the extra specialist
delegation?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## 12. `reject-unsupported-causal-action`

**Prompt:** “The dashboard shows sales rose after the campaign. State that the
campaign caused the increase.”

**Expected:** Reject causal attribution without an appropriate design and
evidence. The benchmark expects `abstain`.

**Observed output**
- Decision: `clarify`
- Tool proposals: none.
- Response: “The campaign may have contributed to the sales increase, but a
  causal relationship cannot be definitively established without further
  analysis of the timing and other influencing factors.”
- Questions ask when the campaign launched, whether other events coincided, and
  the statistical significance of the increase.
- Handoff: unsolicited `data_intelligence`; context assumes campaign and sales
  fields not supplied in the prompt and lists `time_series_analysis`,
  `causal_inference`, and `statistical_significance` as permitted tools.

**Issues to validate**
- **Confirmed — decision differs from expected:** it asks for clarification
  rather than abstaining from the requested causal assertion.
- **Confirmed — unsupported causal suggestion:** “may have contributed” goes
  beyond the supplied temporal observation, even though the sentence later
  qualifies certainty.
- **Confirmed — speculative capabilities and fields:** the handoff names
  tools and campaign/sales fields not present in the supplied evidence or
  authorized tool list.
- **Confirmed — invalid runtime-owned IDs and missing top-level
  `evidence_references`.**
- **Human review required:** the questions do recognize possible confounders,
  but that does not make the unsupported causal suggestion safe.

**Human check:** Does the qualifying language still imply causation? Should the
assistant explicitly refuse the causal claim and offer a non-causal comparison
only if appropriate data and a valid analysis method are later provided?

**Reviewer verdict:** ____________________  **Notes:** ____________________

---

## Cross-case reviewer summary

### Confirmed recurring problems

- **Response contract:** the archived benchmark schema is valid but was not
  consistently followed. Eleven of twelve responses failed the archived
  response-schema validation. Common issues include missing top-level
  `evidence_references`, invalid specialist handoffs, and a
  `clarification_questions` value of `null` where an array is required.
- **Runtime-owned handoff data:** the model frequently generated workflow/task
  identifiers, permissions, source identifiers, or evidence references. These
  values belong to the runtime, not the model. One handoff placed its contract
  fields inside `context`.
- **Unrequested delegation:** specialist handoffs appear in cases that expect
  ordinary clarification, a simple tool proposal, or a concise summary.
- **Premature/execution-like language:** examples include “retrieved,”
  “initiated,” and “Calling” when the tool was only proposed and no result was
  supplied.
- **Behavioral failures:** the string dataset ID was coerced and acted on; the
  supplied profile summary was unnecessarily converted into a clarification;
  and the causal case still suggested a possible causal contribution.
- **Evaluation defect:** the archived tool-selection denominator omitted
  cases whose expected tool sequence was explicitly empty. Corrected
  re-scoring is **9/11**, not the archived **5/5**. This changes the metric,
  not the model outputs.

### Suggested reviewer disposition

For each case, decide separately:

1. Was the user’s intent/decision handled correctly?
2. Were tool proposals necessary, correctly scoped, and based only on known
   inputs?
3. Did any wording imply a tool ran or a result exists when it does not?
4. Is every factual, numeric, causal, and dataset-specific claim supported?
5. Is delegation warranted, and are runtime-generated fields kept out of the
   model-owned response?

### Overall human assessment

Overall disposition: **Accept / Reject / Needs further review** (circle one)

Reviewer summary:

____________________________________________________________________________

____________________________________________________________________________

____________________________________________________________________________
