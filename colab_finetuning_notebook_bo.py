# -*- coding: utf-8 -*-
import os
import pandas as pd
from transformers import pipeline
from transformers import AutoTokenizer, DataCollatorForSeq2Seq, AutoModelForSeq2SeqLM, Seq2SeqTrainingArguments, Seq2SeqTrainer
import os

import pandas as pd
from pprint import pprint
from abc import ABC, abstractmethod
"""colab_finetuning_notebook_BØ.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1zr-k-AgvP2swNvLGhZg-rfGoIA5C03G8
"""

#These two lines of code needs to be run only the first time the notebook is run in colab, to allow colab access to your files.

HUGGING_FACE_WRITE_TOKEN = os.environ.get('HUGGING_FACE_ACCESS_TOKEN')
HUGGING_FACE_READ_TOKEN = os.environ.get('HUGGING_FACE_READ_TOKEN')

if HUGGING_FACE_WRITE_TOKEN is None:
    print("Token is not set.")
else:
    print("Token:", HUGGING_FACE_WRITE_TOKEN)

"""### Imports"""

from huggingface_hub import notebook_login
from transformers import AutoTokenizer, DataCollatorForSeq2Seq, AutoModelForSeq2SeqLM, Seq2SeqTrainingArguments, Seq2SeqTrainer, GenerationConfig
from peft import PeftModel, PeftConfig, get_peft_model, LoraConfig

import evaluate
import os
from datasets import Dataset
import numpy as np

rouge = evaluate.load("rouge")

"""### Class"""

class HuggingFaceFineTuner:

    def __init__(self, model_checkpoint: str):

        self.model_checkpoint = model_checkpoint

        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_checkpoint)
        self.tokenizer = AutoTokenizer.from_pretrained(model_checkpoint, add_prefix_space=True, token=HUGGING_FACE_READ_TOKEN)
        self.data_collator = DataCollatorForSeq2Seq(tokenizer=self.tokenizer, model=model_checkpoint)

        print(self.model)

        self.peft_conf = LoraConfig(
            task_type = "SEQ_2_SEQ_LM", # Sequence2Sequence Language Model,
            r=4,
            lora_alpha=32,
            lora_dropout=0.01,
            target_modules=['q_proj'] # Apply LoRA to "Query layer"
        )

        self.training_args = Seq2SeqTrainingArguments(
            output_dir="models/",
            evaluation_strategy="epoch",
            learning_rate=0.01,
            per_device_train_batch_size=3,
            per_device_eval_batch_size=3,
            weight_decay=0.005,
            save_total_limit=3,
            num_train_epochs=4,
            predict_with_generate=True,
            push_to_hub=False
        )

    def get_model(self):
        return self.model

    def get_tokenizer(self):
        return self.tokenizer

    def get_tuning_data(self, data_path: str) -> pd.DataFrame:
        """
        Retrieves excel file, and returns it in dataframe
        """
        return pd.read_excel(data_path)


    def tokenize_function(self, examples: Dataset):
        """
        Turn text into numbers
        """
        prompt_max_len = 4096
        label_max_len = 4096

        text = examples['prompt']
        target = examples['reference_summary']

        model_inputs = self.tokenizer(text, truncation=True)
        model_inputs["labels"] = self.tokenizer(text_target=target, truncation=True)["input_ids"]

        return model_inputs

    def preprocess_tuning_data(self, data: pd.DataFrame) -> Dataset:
        """
        Cleans and tokenizes data
        """

        ############## CLEAN ##############

        # # Prompt v0
        # prompt_prefix = 'Summarize the following using less than 250 words and emphasizing technical data: '

        # data = data[["content", "reference_summary"]]

        # # Removes pre and post-fix "['" on the summary
        # data["prompt"] = prompt_prefix + data["content"]
        # #data['reference_summary'] = data["reference_summary"].str[1:-1]

        # Prompt v1
        # prompt_prefix = """
        #             You're an expert in the energy sector.
        #             You write good, concise and entity dense summaries within the energy sector.

        #             You will be provided with an article text about the energy sector.
        #             It is your job to provide a summary of the article text, focusing on important entities and numbers in the article.
        #             The length of the summary should be between 105 and 170 words.

        #             Please use the article text to generate a summary that is:
        #             - Factual.
        #             - Precise and entity-dense.
        #             - Not directly paraphrased from the article.
        #             - Useful from the point of view of an energy analyst.
        #             - Do not include sales- or marketing-like language in the summary. If a company says that a project is "showcasing their expertise in geothermal energy projects", you should not include that in the bullet points since that is considered marketing language.
        #             - Do not include political statements or politically loaded language in the article.

        #             Please provide a summary of the following article text: \n\n<article>\n
        #             """
        # prompt_suffix = """\n</article>"""

        # Prompt v2
        # prompt_prefix = "<article>"
        # prompt_suffix = "<\article>"

        # Prompt v3
        prompt_prefix = "Summarize the following article: \n<article>"
        prompt_suffix = "</article>"

        data = data[["content", "reference_summary"]]

        # Removes pre and post-fix "['" on the summary
        data["prompt"] = prompt_prefix + data["content"] + prompt_suffix


        ############## TOKENIZE ##############
        dataset = Dataset.from_pandas(data)
        dataset = dataset.map(self.tokenize_function, batched=True)

        return dataset


    def compute_metrics(self, eval_pred) -> dict:
        """
        Computes various metrics given the predicted and target label (eval_pred).
        Returns dictionary in form {"metric": score}
        """
        predictions, labels = eval_pred

        decoded_preds = self.tokenizer.batch_decode(predictions, skip_special_tokens=True)
        labels = np.where(labels != -100, labels, self.tokenizer.pad_token_id)
        decoded_labels = self.tokenizer.batch_decode(labels, skip_special_tokens=True)

        result = rouge.compute(predictions=decoded_preds, references=decoded_labels, use_stemmer=True)

        prediction_lens = [np.count_nonzero(pred != self.tokenizer.pad_token_id) for pred in predictions]
        result["gen_len"] = np.mean(prediction_lens)

        return {k: round(v, 4) for k, v in result.items()}


    def perform_fine_tuning(self, data: pd.DataFrame, test_size):
        dataset = self.preprocess_tuning_data(data)

        dataset = dataset.train_test_split(test_size=test_size)

        # Enable parameter-efficient fine-tuning, in this case using LoRA
        self.model = get_peft_model(self.model, self.peft_conf)
        self.model.print_trainable_parameters()

        trainer = Seq2SeqTrainer(
            model=self.model,
            args=self.training_args,
            train_dataset=dataset['train'],
            eval_dataset=dataset['test'],
            tokenizer=self.tokenizer,
            data_collator=self.data_collator,
            compute_metrics=self.compute_metrics
        )

        trainer.train()

        self.model = trainer.model

    def store_fine_tuned_model(self, path: str = "models") -> None:
        self.model.save_pretrained(f"{path}/model")
        gen_config = GenerationConfig.from_model_config(self.model.config)
        gen_config.save_pretrained(f"{path}/config", "gen_config.json")
        self.tokenizer.save_pretrained(f"{path}/tokenizer")

    def push_to_huggingface_hub(self, model_checkpoint: str, hf_token) -> None:
        self.get_model().push_to_hub(model_checkpoint, token=hf_token)
        self.get_tokenizer().push_to_hub(model_checkpoint, token=hf_token)

"""### Testing"""

if __name__ == "__main__":
    # Load data as pandas-file.
    path = "summary_1823.json"
    # path = "summary_1823.json"
    full_data = pd.read_json(path)
    full_data = full_data.dropna()
    data = full_data[-1823:]
    print(len(data))

    """
    ### Finetuning loop
    """

    import time

    article_counts = [250, 1000, 1500]
    current_version = "v4"
    models = ["meta-llama/Meta-Llama-3-70B"]
    times = {}

    for model_checkpoint in models:
      for article_count in article_counts:
        start = time.time()
        print(f"Finetuning {model_checkpoint} on {article_count} articles")
        data = full_data[-article_count:]
        print(len(data))
        hfft = HuggingFaceFineTuner(model_checkpoint)

        hfft.perform_fine_tuning(data, test_size=0.2)

        hf_token = HUGGING_FACE_WRITE_TOKEN
        model_name = model_checkpoint.split('/')[1]
        fine_tuned_model_checkpoint = f"relu-ntnu/{model_name}_{current_version}_trained_on_{article_count}"
        hfft.push_to_huggingface_hub(fine_tuned_model_checkpoint, hf_token=hf_token)

        time_used = round(time.time() - start, 0)
        times[fine_tuned_model_checkpoint] = time_used
        print(times)

    from pprint import pprint

    pprint(times)