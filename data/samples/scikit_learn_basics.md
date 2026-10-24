# Machine learning with scikit-learn

## Train and test split

Hold out data with train_test_split(X, y, test_size=0.2, random_state=42) so the model is scored on examples it never saw. Fix random_state to make experiments reproducible.

## Estimators and pipelines

Every estimator implements fit and predict. A Pipeline chains preprocessing such as StandardScaler with a model so the same transformations are applied during training and prediction and data leakage is avoided.

## Cross validation

cross_val_score with cv=5 trains on five different folds and reports the spread of scores. Use StratifiedKFold when classes are imbalanced so every fold keeps the class proportions.

## Clustering with KMeans

KMeans groups samples into n_clusters clusters by minimising within cluster variance. Choose the number of clusters with the elbow method or the silhouette score, and set n_init because the result depends on the random starting centroids.

## Metrics

Accuracy misleads on imbalanced data, so use precision, recall and f1_score. The confusion matrix shows the error types, and roc_auc_score summarises ranking quality across thresholds.
