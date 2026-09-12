# rubric judge, v1

You are grading an AI assistant's answer against a free-text rubric.
Score 0-10 where 10 means every point of the rubric is fully met and 0
means none is. A factual error caps the score at 3. Do not reward length,
tone or confidence; a short correct answer beats a long vague one.

## Question
${question}

## Answer
${answer}

## Rubric
${rubric}

Reply with ONLY a JSON object on one line:
{"score": <integer 0-10>, "reason": "<one sentence naming what was met or missed>"}
