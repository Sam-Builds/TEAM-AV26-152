"""
MODEL TRAINER — Fine-tunes BERTweet on CrisisNLP disaster dataset
BERTweet is trained on 850M real tweets — perfect base for disaster detection
Run this ONCE on Google Colab (free GPU) before the hackathon
Saves a fine-tuned model you load during demo
"""

import os
import numpy as np
import pandas as pd
from datasets import Dataset, DatasetDict
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer,
    EarlyStoppingCallback,
)
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score,
    precision_score, recall_score, confusion_matrix
)
import torch
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────

MODEL_NAME   = "vinai/bertweet-base"          # Twitter-specific BERT (850M tweets)
OUTPUT_DIR   = "./bertweet_disaster_model"
DATASET_PATH = "./crisis_nlp_data.csv"        # Download from: https://crisisnlp.qcri.org
MAX_LENGTH   = 128                            # Max tweet tokens
BATCH_SIZE   = 16
EPOCHS       = 4
LEARNING_RATE = 2e-5

LABEL2ID = {
    "not_disaster":      0,
    "flood":             1,
    "earthquake":        2,
    "fire":              3,
    "cyclone":           4,
    "infrastructure":    5,
    "medical":           6,
}
ID2LABEL = {v: k for k, v in LABEL2ID.items()}
NUM_LABELS = len(LABEL2ID)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")


# ─────────────────────────────────────────────────────────
# STEP 1 — LOAD & PREPARE DATA
# CrisisNLP CSV must have columns: "text", "label"
# If you don't have it yet, we generate synthetic data below
# ─────────────────────────────────────────────────────────

def load_data():
    if os.path.exists(DATASET_PATH):
        df = pd.read_csv(DATASET_PATH)
        print(f"Loaded {len(df)} samples from {DATASET_PATH}")
    else:
        print("CrisisNLP CSV not found — generating synthetic training data...")
        df = generate_synthetic_data()

    # Map string labels to int IDs
    df["label"] = df["label"].map(LABEL2ID)
    df = df.dropna(subset=["label"])
    df["label"] = df["label"].astype(int)
    return df


def generate_synthetic_data():
    """
    Realistic synthetic disaster tweets for each category.
    Replace with real CrisisNLP data for better accuracy.
    """
    samples = [
        # not_disaster
        ("Had the best biryani at this place in Hyderabad, totally recommend!", "not_disaster"),
        ("New movie releasing this Friday, can't wait to watch!", "not_disaster"),
        ("Traffic is bad today on the expressway but nothing unusual", "not_disaster"),
        ("Celebrating my birthday with family today, so happy!", "not_disaster"),
        ("The weather is pleasant in Pune, great day for a walk", "not_disaster"),
        ("Just finished reading an amazing book, highly recommend", "not_disaster"),
        ("Watched the cricket match last night, what a game!", "not_disaster"),
        ("New cafe opened near my office, good coffee", "not_disaster"),

        # flood
        ("Massive flooding in Chennai, people stranded on rooftops need rescue NOW", "flood"),
        ("Velachery completely submerged, no power, no food, please send help #ChennaiFloods", "flood"),
        ("Water level rising rapidly near Hussain Sagar lake, evacuate immediately", "flood"),
        ("Flood waters entering ground floor homes in Dharavi Mumbai, families trapped", "flood"),
        ("Roads completely inundated near Patna, rescue boats needed urgently", "flood"),
        ("Heavy waterlogging in Bengaluru, cars submerged, people stuck on flyovers", "flood"),
        ("River Brahmaputra overflowing, villages in Assam evacuated, crops destroyed", "flood"),
        ("Flash flood warning issued for coastal Kerala districts, people moving to higher ground", "flood"),

        # earthquake
        ("Strong earthquake tremors felt across Jaipur, buildings cracked, people on streets", "earthquake"),
        ("6.2 magnitude quake hits Uttarakhand, multiple aftershocks reported", "earthquake"),
        ("Earthquake in Manipur, several buildings collapsed in Imphal city center", "earthquake"),
        ("Tremors felt in Delhi NCR, residents evacuating high-rises as precaution", "earthquake"),
        ("Seismic activity reported near Bhuj Gujarat, people fleeing homes", "earthquake"),
        ("Earthquake aftershock felt in Nepal border areas, rescue teams deployed", "earthquake"),
        ("Buildings collapsed after major tremor in Aizawl, rescue operations underway", "earthquake"),

        # fire
        ("Massive fire breaks out in Dharavi slum Mumbai, fire brigade on scene", "fire"),
        ("Chemical factory blaze in Surat, toxic smoke spreading, residents evacuating", "fire"),
        ("Forest fire spreading rapidly in Uttarakhand hills, villages at risk", "fire"),
        ("Building on fire in Sarojini Nagar Delhi, people trapped on upper floors", "fire"),
        ("Wildfire near Ooty spreading fast, wind not helping, firefighters struggling", "fire"),
        ("Textile market fire in Surat, multiple shops gutted, losses in crores", "fire"),

        # cyclone
        ("Cyclone Biparjoy approaching Gujarat coast, wind speed 180kmph, evacuations begin", "cyclone"),
        ("Storm surge warning for Odisha coast, fishermen asked not to venture out", "cyclone"),
        ("Cyclone landfall imminent near Vishakhapatnam, heavy rain and strong winds", "cyclone"),
        ("Typhoon-like conditions in Andaman Islands, airport closed, ships halted", "cyclone"),
        ("Hurricane-force winds hitting Tamil Nadu coast, trees uprooted, power out", "cyclone"),

        # infrastructure
        ("Bridge collapsed in Bihar, several vehicles fell into river, rescue ongoing", "infrastructure"),
        ("Flyover partially collapsed in Kolkata, traffic disrupted, workers trapped", "infrastructure"),
        ("Dam breach reported near Pune, downstream villages on high alert", "infrastructure"),
        ("Building collapse in Bhiwandi thane, 3 floors down, residents trapped inside", "infrastructure"),
        ("Road cave-in swallows vehicles in Chennai, NDRF team deployed", "infrastructure"),

        # medical
        ("Cholera outbreak reported in flood-affected Assam villages, urgent medicines needed", "medical"),
        ("Dengue cases surging in Delhi post-monsoon, hospitals overwhelmed", "medical"),
        ("COVID cluster detected in Dharavi, contact tracing underway", "medical"),
        ("Snake bite deaths rising in flood-hit UP villages, anti-venom shortage", "medical"),
        ("Heatstroke deaths in Rajasthan, 45 degrees, hospitals at capacity", "medical"),
    ]

    df = pd.DataFrame(samples, columns=["text", "label"])
    # Augment to 500+ samples by repeating with small variation
    augmented = []
    for _, row in df.iterrows():
        augmented.append(row)
        augmented.append({"text": row["text"].lower(), "label": row["label"]})
        augmented.append({"text": row["text"].upper(), "label": row["label"]})

    return pd.DataFrame(augmented).reset_index(drop=True)


# ─────────────────────────────────────────────────────────
# STEP 2 — TOKENIZE
# BERTweet has its own tokenizer that handles tweet quirks
# ─────────────────────────────────────────────────────────

def tokenize_data(df):
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, use_fast=False)

    train_df, temp_df = train_test_split(df, test_size=0.3, random_state=42, stratify=df["label"])
    val_df, test_df   = train_test_split(temp_df, test_size=0.5, random_state=42, stratify=temp_df["label"])

    def tokenize_batch(batch):
        return tokenizer(
            batch["text"],
            max_length=MAX_LENGTH,
            truncation=True,
            padding="max_length",
        )

    def to_hf_dataset(df):
        ds = Dataset.from_pandas(df[["text", "label"]].reset_index(drop=True))
        return ds.map(tokenize_batch, batched=True)

    dataset = DatasetDict({
        "train": to_hf_dataset(train_df),
        "val":   to_hf_dataset(val_df),
        "test":  to_hf_dataset(test_df),
    })
    print(f"Train: {len(dataset['train'])}  Val: {len(dataset['val'])}  Test: {len(dataset['test'])}")
    return dataset, tokenizer


# ─────────────────────────────────────────────────────────
# STEP 3 — DEFINE METRICS
# F1-macro is the right metric for imbalanced disaster classes
# ─────────────────────────────────────────────────────────

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    return {
        "accuracy":  accuracy_score(labels, predictions),
        "f1_macro":  f1_score(labels, predictions, average="macro", zero_division=0),
        "precision": precision_score(labels, predictions, average="macro", zero_division=0),
        "recall":    recall_score(labels, predictions, average="macro", zero_division=0),
    }


# ─────────────────────────────────────────────────────────
# STEP 4 — TRAIN
# ─────────────────────────────────────────────────────────

def train_model(dataset):
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=NUM_LABELS,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
        ignore_mismatched_sizes=True,
    )

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        learning_rate=LEARNING_RATE,
        weight_decay=0.01,
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        logging_dir="./logs",
        logging_steps=10,
        fp16=torch.cuda.is_available(),          # use half precision on GPU
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["val"],
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )

    print("\n🚀 Starting fine-tuning BERTweet...")
    trainer.train()

    # Evaluate on test set
    print("\n📊 Test Set Evaluation:")
    results = trainer.evaluate(dataset["test"])
    for k, v in results.items():
        print(f"  {k}: {v:.4f}")

    trainer.save_model(OUTPUT_DIR)
    print(f"\n✅ Model saved to {OUTPUT_DIR}")
    return model


# ─────────────────────────────────────────────────────────
# STEP 5 — CONFUSION MATRIX (shows where model struggles)
# ─────────────────────────────────────────────────────────

def print_confusion_matrix(model, dataset, tokenizer):
    model.eval()
    preds, labels = [], []
    for batch in dataset["test"]:
        with torch.no_grad():
            inputs = {
                k: torch.tensor([v]).to(device)
                for k, v in batch.items()
                if k in ["input_ids", "attention_mask"]
            }
            output = model(**inputs)
            pred = torch.argmax(output.logits, dim=-1).item()
            preds.append(pred)
            labels.append(batch["label"])

    cm = confusion_matrix(labels, preds)
    print("\nConfusion Matrix:")
    print(pd.DataFrame(cm, index=ID2LABEL.values(), columns=ID2LABEL.values()))


# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("BERTweet Fine-Tuning — Disaster Tweet Classifier")
    print("=" * 60)

    df = load_data()
    print(f"\nLabel distribution:\n{df['label'].value_counts()}\n")

    dataset, tokenizer = tokenize_data(df)
    model = train_model(dataset)
    print_confusion_matrix(model, dataset, tokenizer)

    print("\n✅ Training complete. Use this model in nlp_pipeline.py")