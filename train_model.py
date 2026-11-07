#!/usr/bin/env python3
"""
Train LightGBM Ranker for NBA First Basket Prediction
Objective: NDCG@1 > 0.25 (beats heuristic ~0.18)
"""

import json
import numpy as np
import lightgbm as lgb
from datetime import date

# ──────────────────────────────────────────────
# LOAD DATA
# ──────────────────────────────────────────────
X = np.load('X_train.npy')
y = np.load('y_train.npy')
groups = np.load('groups.npy')

with open('feature_names.json') as f:
    feature_names = json.load(f)

with open('meta.json') as f:
    meta = json.load(f)

print(f"Data: {X.shape[0]} samples, {X.shape[1]} features, {len(groups)} games")
print(f"Features: {feature_names}")
print(f"Positive rate: {y.mean():.3f}")

# ──────────────────────────────────────────────
# TIME-SERIES SPLIT (by game order = chronological)
# ──────────────────────────────────────────────
# 642 games: ~60% train (385), ~20% val (128), ~20% test (129)
n_games = len(groups)
train_end = int(n_games * 0.6)
val_end = int(n_games * 0.8)

# Compute cumulative indices
cumsum = np.cumsum([0] + groups.tolist())
train_idx = slice(cumsum[0], cumsum[train_end])
val_idx = slice(cumsum[train_end], cumsum[val_end])
test_idx = slice(cumsum[val_end], cumsum[-1])

X_train, y_train, g_train = X[train_idx], y[train_idx], groups[:train_end]
X_val, y_val, g_val = X[val_idx], y[val_idx], groups[train_end:val_end]
X_test, y_test, g_test = X[test_idx], y[test_idx], groups[val_end:]

print(f"\nSplit:")
print(f"  Train: {len(g_train)} games, {X_train.shape[0]} samples")
print(f"  Val:   {len(g_val)} games, {X_val.shape[0]} samples")
print(f"  Test:  {len(g_test)} games, {X_test.shape[0]} samples")

# ──────────────────────────────────────────────
# LIGHTGBM RANKER
# ──────────────────────────────────────────────
params = {
    'objective': 'lambdarank',
    'metric': 'ndcg',
    'eval_at': [1, 3, 5],
    'boosting_type': 'gbdt',
    'learning_rate': 0.05,
    'num_leaves': 31,
    'max_depth': -1,
    'min_data_in_leaf': 50,
    'feature_fraction': 0.8,
    'bagging_fraction': 0.8,
    'bagging_freq': 5,
    'lambda_l1': 0.1,
    'lambda_l2': 0.1,
    'verbose': -1,
    'seed': 42,
    'n_estimators': 500,
    'early_stopping_rounds': 50,
}

print("\nTraining LightGBM Ranker...")
train_data = lgb.Dataset(X_train, label=y_train, group=g_train, feature_name=feature_names)
val_data = lgb.Dataset(X_val, label=y_val, group=g_val, feature_name=feature_names, reference=train_data)

model = lgb.train(
    params,
    train_data,
    valid_sets=[train_data, val_data],
    valid_names=['train', 'val'],
    callbacks=[
        lgb.log_evaluation(period=50),
        lgb.early_stopping(stopping_rounds=50),
    ],
)

# ──────────────────────────────────────────────
# EVALUATE
# ──────────────────────────────────────────────
print("\nEvaluating on validation set...")
val_pred = model.predict(X_val, num_iteration=model.best_iteration)

# Compute NDCG@1 manually per game
from sklearn.metrics import ndcg_score

val_ndcg1 = []
val_ndcg3 = []
val_ndcg5 = []

start = 0
for g in g_val:
    end = start + g
    y_true = y_val[start:end].reshape(1, -1)
    y_score = val_pred[start:end].reshape(1, -1)

    # NDCG@1
    ndcg1 = ndcg_score(y_true, y_score, k=1)
    ndcg3 = ndcg_score(y_true, y_score, k=min(3, g))
    ndcg5 = ndcg_score(y_true, y_score, k=min(5, g))

    val_ndcg1.append(ndcg1)
    val_ndcg3.append(ndcg3)
    val_ndcg5.append(ndcg5)
    start = end

print(f"Val NDCG@1: {np.mean(val_ndcg1):.4f}")
print(f"Val NDCG@3: {np.mean(val_ndcg3):.4f}")
print(f"Val NDCG@5: {np.mean(val_ndcg5):.4f}")

# Test set
print("\nEvaluating on test set...")
test_pred = model.predict(X_test, num_iteration=model.best_iteration)

test_ndcg1 = []
test_ndcg3 = []
test_ndcg5 = []

start = 0
for g in g_test:
    end = start + g
    y_true = y_test[start:end].reshape(1, -1)
    y_score = test_pred[start:end].reshape(1, -1)

    ndcg1 = ndcg_score(y_true, y_score, k=1)
    ndcg3 = ndcg_score(y_true, y_score, k=min(3, g))
    ndcg5 = ndcg_score(y_true, y_score, k=min(5, g))

    test_ndcg1.append(ndcg1)
    test_ndcg3.append(ndcg3)
    test_ndcg5.append(ndcg5)
    start = end

print(f"Test NDCG@1: {np.mean(test_ndcg1):.4f}")
print(f"Test NDCG@3: {np.mean(test_ndcg3):.4f}")
print(f"Test NDCG@5: {np.mean(test_ndcg5):.4f}")

# Top-1 accuracy (heuristic comparison)
test_top1_correct = 0
start = 0
for g in g_test:
    end = start + g
    pred_idx = np.argmax(test_pred[start:end])
    true_idx = np.argmax(y_test[start:end])
    if pred_idx == true_idx:
        test_top1_correct += 1
    start = end

test_acc = test_top1_correct / len(g_test)
print(f"Test Top-1 Accuracy: {test_acc:.4f} ({test_top1_correct}/{len(g_test)})")

# ──────────────────────────────────────────────
# FEATURE IMPORTANCE
# ──────────────────────────────────────────────
importance = model.feature_importance(importance_type='gain')
print("\nFeature Importance (gain):")
for name, imp in zip(feature_names, importance):
    print(f"  {name}: {imp}")

# ──────────────────────────────────────────────
# SAVE MODEL
# ──────────────────────────────────────────────
model.save_model('model.txt', num_iteration=model.best_iteration)

model_meta = {
    'model_type': 'LightGBM Ranker',
    'objective': 'lambdarank',
    'metric': 'ndcg',
    'best_iteration': int(model.best_iteration),
    'feature_names': feature_names,
    'n_features': len(feature_names),
    'train_games': len(g_train),
    'val_games': len(g_val),
    'test_games': len(g_test),
    'val_ndcg_at_1': float(np.mean(val_ndcg1)),
    'val_ndcg_at_3': float(np.mean(val_ndcg3)),
    'val_ndcg_at_5': float(np.mean(val_ndcg5)),
    'test_ndcg_at_1': float(np.mean(test_ndcg1)),
    'test_ndcg_at_3': float(np.mean(test_ndcg3)),
    'test_ndcg_at_5': float(np.mean(test_ndcg5)),
    'test_top1_accuracy': float(test_acc),
    'feature_importance': {name: int(imp) for name, imp in zip(feature_names, importance)},
    'params': params,
    'trained_date': date.today().isoformat(),
}

with open('model_meta.json', 'w') as f:
    json.dump(model_meta, f, indent=2)

print("\n✅ Saved: model.txt, model_meta.json")

# ──────────────────────────────────────────────
# SUCCESS CRITERIA CHECK
# ──────────────────────────────────────────────
print("\n" + "="*50)
print("SUCCESS CRITERIA:")
print(f"  Test NDCG@1 > 0.25: {'✅ PASS' if np.mean(test_ndcg1) > 0.25 else '❌ FAIL'} ({np.mean(test_ndcg1):.4f})")
print(f"  Test Top-1 > 0.55:  {'✅ PASS' if test_acc > 0.55 else '❌ FAIL'} ({test_acc:.4f})")
print("="*50)