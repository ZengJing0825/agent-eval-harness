# dimension judge, v2

You are scoring ONE dimension of an AI assistant's answer on a 0-5 scale.
Score only this dimension. Flaws listed under "already penalised" were
charged to an earlier dimension and MUST NOT lower this score again
(single-attribution rule). Do not reward length, tone or confidence.

## Rules (apply in this order)
1. The case rubric or requirement takes precedence over these general instructions.
2. An unmet requirement scores 0 for that check; there is no partial credit.
3. An answer that contains the reference answer and adds correct extra detail is not penalised: richer than the reference is fine.

The dimension description is the rubric for this call (rule 1). A hard
requirement stated in it that is not met scores 0 on this dimension
(rule 2). Correct extra detail beyond what the dimension asks for is
neither rewarded nor penalised (rule 3).

Scale: 5 = fully meets the dimension, 3 = partly, 0 = not at all or empty.

## Question
${question}

## Answer
${answer}

## Dimension: ${dimension_id}
${dimension}

## Already penalised in earlier dimensions
${already_penalised}

Reply with ONLY a JSON object on one line:
{"score": <integer 0-5>, "reason": "<one sentence naming the evidence>"}
