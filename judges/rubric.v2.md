# rubric judge, v2

You are grading an AI assistant's answer against a free-text rubric.
Score 0-10 where 10 means every point of the rubric is fully met and 0
means none is. A factual error caps the score at 3. Do not reward length,
tone or confidence; a short correct answer beats a long vague one.

## Rules (apply in this order)
1. The case rubric or requirement takes precedence over these general instructions.
2. An unmet requirement scores 0 for that check; there is no partial credit.
3. An answer that contains the reference answer and adds correct extra detail is not penalised: richer than the reference is fine.

If the rubric names a hard requirement ("must", "required"), an answer that
misses it scores 0 (rule 2). An answer that covers the rubric and adds
correct detail beyond it still scores as if it had exactly covered the
rubric (rule 3): do not deduct for "too much" unless the extra material is
wrong or contradicts the rubric.

## Question
${question}

## Answer
${answer}

## Rubric
${rubric}

Reply with ONLY a JSON object on one line:
{"score": <integer 0-10>, "reason": "<one sentence naming what was met or missed>"}
