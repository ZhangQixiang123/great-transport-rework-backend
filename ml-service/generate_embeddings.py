"""Generate title embeddings for all competitor videos and save with PCA."""
import sqlite3
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.decomposition import PCA

N_COMPONENTS = 20
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
DB_PATH = "data.db"
OUTPUT_PATH = "models/title_embeddings.npz"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT bvid, title FROM competitor_videos ORDER BY bvid"
    ).fetchall()
    conn.close()

    bvids = [r["bvid"] for r in rows]
    titles = [r["title"] or "" for r in rows]
    print(f"Loaded {len(titles)} titles")

    # Encode
    print(f"Encoding with {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    raw_embeddings = model.encode(titles, show_progress_bar=True, batch_size=256)
    print(f"Raw embeddings shape: {raw_embeddings.shape}")

    # PCA
    print(f"Fitting PCA to {N_COMPONENTS} components...")
    pca = PCA(n_components=N_COMPONENTS, random_state=42)
    reduced = pca.fit_transform(raw_embeddings)
    explained = pca.explained_variance_ratio_.sum()
    print(f"PCA explained variance: {explained:.3f}")
    print(f"Reduced shape: {reduced.shape}")

    # Save: bvids array + embedding matrix + PCA components for inference
    np.savez(
        OUTPUT_PATH,
        bvids=np.array(bvids),
        embeddings=reduced.astype(np.float32),
        pca_components=pca.components_.astype(np.float32),
        pca_mean=pca.mean_.astype(np.float32),
        explained_variance_ratio=pca.explained_variance_ratio_,
    )
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
