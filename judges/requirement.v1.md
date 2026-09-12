# requirement judge, v1

You are checking whether an AI assistant's answer satisfies ONE requirement.
Judge only the requirement below; ignore everything else about the answer.
If a calculation is provided, use it as the reference for what a correct
answer must contain. Do not reward length, tone or confidence.

## Question
${question}

## Answer
${answer}

## Requirement
${requirement}

## Reference calculation (may be empty)
${calculation}

Reply with ONLY a JSON object on one line:
{"verdict": "yes" | "no", "reason": "<one sentence pointing at the evidence>"}
