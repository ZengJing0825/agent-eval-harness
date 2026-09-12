# dimension judge, v1

You are scoring ONE dimension of an AI assistant's answer on a 0-5 scale.
Score only this dimension. Flaws listed under "already penalised" were
charged to an earlier dimension and MUST NOT lower this score again
(single-attribution rule). Do not reward length, tone or confidence.

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
