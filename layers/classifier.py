"""
Agent Café — Injection Classifier
Binary classifier: "Is this text trying to modify the reader's behavior?"

Architecture:
  TF-IDF (word + character n-grams) → Logistic Regression
  
  - Fully local, no API calls
  - ~1ms inference
  - Trained on red team data + synthetic examples
  - Returns probability score (0.0 = clean, 1.0 = injection)

Usage:
  from layers.classifier import InjectionClassifier
  clf = InjectionClassifier()
  score = clf.predict("some text")  # Returns 0.0 - 1.0
  is_bad = clf.is_injection("some text")  # Returns bool (threshold: 0.5)
"""

import json
import os
import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.model_selection import cross_val_score
from sklearn.metrics import classification_report


MODEL_DIR = Path(__file__).parent / "classifier_model"
MODEL_PATH = MODEL_DIR / "injection_classifier.pkl"
DATA_PATH = Path(__file__).parent / "classifier_data.json"


class InjectionClassifier:
    """
    Binary classifier for detecting behavioral manipulation in text.
    
    Uses TF-IDF features (word + character n-grams) with Logistic Regression.
    Fully local inference, no API calls. ~1ms per prediction.
    """
    
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.pipeline: Optional[Pipeline] = None
        self._load_or_train()
    
    def _load_or_train(self):
        """Load saved model or train from data."""
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, "rb") as f:
                    self.pipeline = pickle.load(f)
                return
            except Exception:
                pass  # Retrain if load fails
        
        # Train from data
        if DATA_PATH.exists():
            self.train_from_file(DATA_PATH)
        else:
            print("⚠️  No classifier data found. Classifier disabled.")
    
    def train_from_file(self, data_path: Path = None) -> dict:
        """Train the classifier from labeled data file."""
        path = data_path or DATA_PATH
        
        with open(path) as f:
            data = json.load(f)
        
        texts = [s["text"] for s in data["samples"]]
        labels = [s["label"] for s in data["samples"]]
        
        return self.train(texts, labels)
    
    def train(self, texts: list, labels: list) -> dict:
        """
        Train the classifier.
        
        Returns metrics dict with accuracy, cross-validation scores, 
        and per-class precision/recall.
        """
        # Feature engineering: combine word-level and character-level TF-IDF
        # Word n-grams catch phrase patterns ("ignore instructions")
        # Character n-grams catch obfuscation ("1gn0r3", homoglyphs)
        self.pipeline = Pipeline([
            ("features", FeatureUnion([
                ("word_tfidf", TfidfVectorizer(
                    analyzer="word",
                    ngram_range=(1, 3),      # Unigrams through trigrams
                    max_features=5000,
                    sublinear_tf=True,        # Log-scale TF
                    min_df=1,
                    strip_accents="unicode",
                )),
                ("char_tfidf", TfidfVectorizer(
                    analyzer="char_wb",       # Character n-grams within word boundaries
                    ngram_range=(3, 6),       # 3-6 character sequences
                    max_features=5000,
                    sublinear_tf=True,
                    min_df=1,
                    strip_accents="unicode",
                )),
            ])),
            ("classifier", LogisticRegression(
                C=1.0,                        # Regularization
                max_iter=1000,
                class_weight="balanced",      # Handle class imbalance
                solver="lbfgs",
                random_state=42,
            ))
        ])
        
        X = texts
        y = np.array(labels)
        
        # Train
        self.pipeline.fit(X, y)
        
        # Cross-validation (5-fold)
        cv_scores = cross_val_score(self.pipeline, X, y, cv=min(5, len(texts) // 2), scoring="accuracy")
        
        # Full classification report
        y_pred = self.pipeline.predict(X)
        report = classification_report(y, y_pred, target_names=["legit", "injection"], output_dict=True)
        
        # Save model
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self.pipeline, f)
        
        metrics = {
            "samples": len(texts),
            "injection_samples": int(sum(y)),
            "legit_samples": int(len(y) - sum(y)),
            "cv_accuracy_mean": float(cv_scores.mean()),
            "cv_accuracy_std": float(cv_scores.std()),
            "train_accuracy": float(report["accuracy"]),
            "injection_precision": float(report["injection"]["precision"]),
            "injection_recall": float(report["injection"]["recall"]),
            "injection_f1": float(report["injection"]["f1-score"]),
            "legit_precision": float(report["legit"]["precision"]),
            "legit_recall": float(report["legit"]["recall"]),
            "legit_f1": float(report["legit"]["f1-score"]),
        }
        
        return metrics
    
    def predict(self, text: str) -> float:
        """
        Predict injection probability for a text.
        
        Returns float 0.0 (definitely legit) to 1.0 (definitely injection).
        Returns 0.0 if classifier is not loaded.
        """
        if not self.pipeline:
            return 0.0
        
        try:
            proba = self.pipeline.predict_proba([text])[0]
            # proba[1] is probability of class 1 (injection)
            return float(proba[1])
        except Exception:
            return 0.0
    
    def is_injection(self, text: str) -> bool:
        """Binary prediction: is this text an injection attempt?"""
        return self.predict(text) >= self.threshold
    
    def predict_batch(self, texts: list) -> list:
        """Predict injection probability for a batch of texts."""
        if not self.pipeline:
            return [0.0] * len(texts)
        
        try:
            probas = self.pipeline.predict_proba(texts)
            return [float(p[1]) for p in probas]
        except Exception:
            return [0.0] * len(texts)
    
    def explain(self, text: str) -> dict:
        """
        Explain why a text was classified as injection or legit.
        Returns top contributing features.
        """
        if not self.pipeline:
            return {"error": "Classifier not loaded"}
        
        score = self.predict(text)
        
        # Get feature names and coefficients
        try:
            feature_union = self.pipeline.named_steps["features"]
            classifier = self.pipeline.named_steps["classifier"]
            
            # Transform text to features
            features = feature_union.transform([text])
            
            # Get feature names
            feature_names = []
            for name, transformer in feature_union.transformer_list:
                feature_names.extend(
                    [f"{name}:{fn}" for fn in transformer.get_feature_names_out()]
                )
            
            # Get coefficients (weights)
            coefs = classifier.coef_[0]
            
            # Get non-zero features for this text
            nonzero = features.nonzero()[1]
            feature_contributions = []
            for idx in nonzero:
                feature_contributions.append({
                    "feature": feature_names[idx],
                    "weight": float(coefs[idx]),
                    "tfidf_value": float(features[0, idx]),
                    "contribution": float(coefs[idx] * features[0, idx])
                })
            
            # Sort by absolute contribution
            feature_contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
            
            return {
                "score": score,
                "prediction": "injection" if score >= self.threshold else "legit",
                "confidence": max(score, 1 - score),
                "top_features": feature_contributions[:15],
            }
        except Exception as e:
            return {"score": score, "error": str(e)}
    
    def add_sample(self, text: str, label: int, source: str = "live"):
        """
        Add a new training sample and retrain.
        Called when the system learns from a new kill or false positive.
        """
        if not DATA_PATH.exists():
            return
        
        with open(DATA_PATH) as f:
            data = json.load(f)
        
        data["samples"].append({
            "text": text,
            "label": label,
            "source": source
        })
        
        with open(DATA_PATH, "w") as f:
            json.dump(data, f, indent=2)
        
        # Retrain with new data
        return self.train_from_file()
    
    @property
    def is_loaded(self) -> bool:
        return self.pipeline is not None


# Singleton instance
_classifier_instance: Optional[InjectionClassifier] = None

def get_classifier() -> InjectionClassifier:
    """Get or create the singleton classifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = InjectionClassifier()
    return _classifier_instance
