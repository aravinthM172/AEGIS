import json
import boto3

from app.config import AWS_REGION, BEDROCK_MODEL_ID


class BedrockService:

    def __init__(self):
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION
        )

    def generate(self, prompt):

        response = self.client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ],
                "inferenceConfig": {
                    "maxTokens": 800,
                    "temperature": 0.2
                }
            })
        )

        body = json.loads(response["body"].read())

        return body["output"]["message"]["content"][0]["text"]
