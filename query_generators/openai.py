import time
from typing import Dict, List
from func_timeout import FunctionTimedOut, func_timeout
import openai
import tiktoken

from query_generators.query_generator import QueryGenerator
from utils.pruning import prune_metadata_str


class OpenAIQueryGenerator(QueryGenerator):
    """
    Query generator that uses OpenAI's models
    Models available: gpt-3.5-turbo-0613, gpt-4-0613, text-davinci-003
    """

    def __init__(
        self,
        db_creds: Dict[str, str],
        model: str,
        prompt_file: str,
        timeout: int,
        verbose: bool,
        **kwargs,
    ):
        self.db_creds = db_creds
        self.db_name = db_creds["database"]
        self.model = model
        self.prompt_file = prompt_file

        self.timeout = timeout
        self.verbose = verbose

    def get_chat_completion(
        self,
        model,
        messages,
        max_tokens=600,
        temperature=0,
        stop=["```"],
        logit_bias={},
    ):
        """Get OpenAI chat completion for a given prompt and model"""
        generated_text = ""
        try:
            completion = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                logit_bias=logit_bias,
            )
            generated_text = completion["choices"][0]["message"]["content"]
        except (openai.error.RateLimitError, openai.error.ServiceUnavailableError) as e:
            if self.verbose:
                print("Model overloaded. Pausing for 5s before retrying...")
            time.sleep(5)
            # Retry the api call after 5s
            completion = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                logit_bias=logit_bias,
            )
            generated_text = completion["choices"][0]["message"]["content"]
        except Exception as e:
            if self.verbose:
                print(type(e), e)
        return generated_text

    def get_nonchat_completion(
        self,
        model,
        prompt,
        max_tokens=600,
        temperature=0,
        stop=["```"],
        logit_bias={},
    ):
        """Get OpenAI nonchat completion for a given prompt and model"""
        generated_text = ""
        try:
            completion = openai.Completion.create(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                logit_bias=logit_bias,
            )
            generated_text = completion["choices"][0]["text"]
        except (openai.error.RateLimitError, openai.error.ServiceUnavailableError) as e:
            if self.verbose:
                print("Model overloaded. Pausing for 5s before retrying...")
            time.sleep(5)
            # Retry the api call after 5s
            completion = openai.ChatCompletion.create(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                logit_bias=logit_bias,
            )
            generated_text = completion["choices"][0]["text"]
        except Exception as e:
            if self.verbose:
                print(type(e), e)
        return generated_text

    @staticmethod
    def count_tokens(
        model: str, messages: List[Dict[str, str]] = [], prompt: str = ""
    ) -> int:
        """
        This function counts the number of tokens used in a prompt
        model: the model used to generate the prompt. can be one of the following: gpt-3.5-turbo-0613, gpt-4-0613, text-davinci-003
        messages: (only for OpenAI chat models) a list of messages to be used as a prompt. Each message is a dict with two keys: role and content
        prompt: (only for text-davinci-003 model) a string to be used as a prompt
        """
        tokenizer = tiktoken.encoding_for_model(model)
        num_tokens = 0
        if model != "text-davinci-003":
            for message in messages:
                for _, value in message.items():
                    num_tokens += len(tokenizer.encode(value))
        else:
            num_tokens = len(tokenizer.encode(prompt))
        return num_tokens

    def generate_query(self, question: str) -> dict:
        start_time = time.time()
        self.err = ""
        self.query = ""
        self.reason = ""

        with open(self.prompt_file) as file:
            chat_prompt = file.read()

        if self.model != "text-davinci-003":
            sys_prompt = chat_prompt.split("### Input:")[0]
            user_prompt = chat_prompt.split("### Input:")[1].split("### Response:")[0]
            assistant_prompt = chat_prompt.split("### Response:")[1]

            user_prompt = user_prompt.format(
                user_question=question,
                table_metadata_string=prune_metadata_str(question, self.db_name),
            )

            messages = []
            messages.append({"role": "system", "content": sys_prompt})
            messages.append({"role": "user", "content": user_prompt})
            messages.append({"role": "assistant", "content": assistant_prompt})
        else:
            prompt = chat_prompt.format(
                user_question=question,
                table_metadata_string=prune_metadata_str(question, self.db_name),
            )
        function_to_run = None
        package = None
        if self.model == "text-davinci-003":
            function_to_run = self.get_nonchat_completion
            package = prompt
        else:
            function_to_run = self.get_chat_completion
            package = messages

        try:
            self.completion = func_timeout(
                self.timeout,
                function_to_run,
                args=(
                    self.model,
                    package,
                    400,
                    0,
                    ["```"],
                ),
            )
            results = self.completion
            self.query = results.split("```sql")[-1]
            self.reason = "-"
        except FunctionTimedOut:
            if self.verbose:
                print("generating query timed out")
            self.err = "QUERY GENERATION TIMEOUT"
        except Exception as e:
            if self.verbose:
                print(f"Error while generating query: {type(e)}, {e})")
            self.query = ""
            self.reason = ""
            if isinstance(e, KeyError):
                self.err = f"QUERY GENERATION ERROR: {type(e)}, {e}, Completion: {self.completion}"
            else:
                self.err = f"QUERY GENERATION ERROR: {type(e)}, {e}"

        if self.model == "text-davinci-003":
            tokens_used = self.count_tokens(self.model, prompt=prompt)
        else:
            tokens_used = self.count_tokens(self.model, messages=messages)

        return {
            "query": self.query,
            "reason": self.reason,
            "err": self.err,
            "latency_seconds": time.time() - start_time,
            "tokens_used": tokens_used,
        }
