# Recommendation & Advertising Model Research

## Purpose

A professional research skill for discovering, reproducing, and validating recommendation system and advertising models from academic papers. Covers the full pipeline from paper retrieval to verified implementation with quantitative benchmarks.

## Description

This skill guides the agent through a structured research workflow for recommendation and advertising models. It covers arXiv paper search and download, paper parsing, model implementation (both target and baseline), dataset acquisition, rigorous validation, and iterative optimization — all within a session-scoped workspace.

## Tags

- recommendation-system
- advertising-model
- deep-learning
- CTR-prediction
- research
- paper-reproduction
- model-validation

## When to use

- User wants to research, reproduce, or compare recommendation/advertising models
- User mentions CTR prediction, click-through rate, conversion rate models
- User wants to implement models like DeepFM, DIN, DCN, DIEN, MMoE, PLE, AutoInt, etc.
- User asks to find and reproduce results from a recommendation system paper
- User needs to benchmark models on datasets like Criteo, Avazu, MovieLens, Amazon Reviews
- User wants to explore state-of-the-art in ads/recommendation ranking

## When NOT to use

- Pure NLP tasks (use a general research skill instead)
- Image classification / computer vision tasks
- Simple data analysis without model training
- Non-ML engineering tasks

## Inputs expected

- **Paper reference**: arXiv ID, paper title, or research topic keywords
- **Target model** (optional): Specific model architecture to reproduce
- **Baseline models** (optional): Models to compare against
- **Dataset preference** (optional): Specific dataset or domain (ads, e-commerce, video)
- **Success metric** (optional): Target AUC, LogLoss, or other metric threshold

## Output requirements

- Parsed paper notes in markdown format
- Working model implementation code (PyTorch preferred)
- Baseline model implementations for comparison
- Downloaded and preprocessed dataset (< 200MB)
- Unit tests passing with zero syntax errors
- Experiment results with AUC, LogLoss on test set
- Comparison table: target model vs baselines
- Optimization log documenting parameter tuning attempts

## Workspace structure

All artifacts are stored under the session research directory:

```
{session_dir}/research/recommendation_research/
├── papers/          # Downloaded PDFs and parsed markdown
├── notes/           # Research notes, summaries, insights
├── datasets/        # Downloaded and preprocessed datasets
├── src/             # Model implementations
│   ├── models/      # Model architectures (target + baselines)
│   ├── data/        # Data loaders and preprocessing
│   ├── train.py     # Training script
│   ├── evaluate.py  # Evaluation script
│   └── utils.py     # Shared utilities
├── configs/         # Experiment configurations (hyperparams)
├── scripts/         # Helper scripts (download, preprocess)
├── tests/           # Unit tests for models and data pipeline
├── runs/            # Experiment logs, TensorBoard logs
├── reports/         # Final comparison reports
└── artifacts/       # Saved model checkpoints, plots
├── logs/            # training and testing logs, log file format: for train_{date_time}.log
└── README.md /       # overall the project in detail
```

## Workflow

### Phase 1: Environment Setup

1. Create the workspace directory structure listed above
2. Verify Python environment has required packages: `torch`, `pandas`, `numpy`, `scikit-learn`
3. If missing, create a `requirements.txt` and install dependencies

### Phase 2: Paper Discovery & Retrieval

1. Search arXiv for the target paper using `search_arxiv` tool
2. Download the PDF using `download_arxiv_pdf` tool into `papers/`
3. Convert PDF to markdown using `convert_pdf_to_markdown` tool
4. Save parsed markdown to `papers/{paper_id}.md`
5. Extract key information:
   - Model architecture description
   - Loss function
   - Training procedure
   - Reported metrics and datasets
   - Baseline models mentioned

### Phase 3: Paper Analysis & Notes

1. Write structured research notes to `notes/`:
   - Problem formulation
   - Key innovation / contribution
   - Architecture diagram (text description)
   - Mathematical formulation of the model
   - Training details (optimizer, learning rate, batch size)
   - Reported results table
2. Identify baseline models to implement for comparison
3. Plan implementation approach

### Phase 4: Dataset Acquisition

1. Download an appropriate open-source dataset. Recommended datasets by domain:

   **Advertising / CTR Prediction:**
   - Criteo Display Ads (subsample, ~100MB)
   - Avazu CTR dataset (subsample)
   - iPinYou RTB dataset

   **E-commerce / Recommendation:**
   - MovieLens 1M or 100K
   - Amazon Product Reviews (subset: Electronics or Books)
   - Yelp Reviews (subset)

   **Search / Ranking:**
   - Microsoft LETOR
   - Yahoo Learning to Rank

2. If dataset exceeds 200MB, sample a representative subset
3. Save to `datasets/` with a preprocessing script in `scripts/`
4. Create a data loader in `src/data/` that outputs (features, label) tensors
5. Perform basic EDA: feature counts, label distribution, sample sizes

### Phase 5: Model Implementation

1. Implement the **target model** in `src/models/`:
   - Follow the paper's architecture precisely
   - Use PyTorch `nn.Module` with clear forward pass
   - Support both sparse (categorical) and dense (numerical) features
   - Include proper embedding layers for categorical features

2. Implement **baseline models** (at least 2):
   - **Logistic Regression** (simple baseline)
   - **DeepFM** or **Wide&Deep** (strong baseline)
   - Additional baselines from the paper if mentioned

3. Common architectural patterns for ad/rec models:
   - Embedding layer for sparse features (typical dim: 8-16)
   - Feature interaction layer (FM, cross-network, attention)
   - DNN tower (MLP with ReLU, BatchNorm, Dropout)
   - Binary cross-entropy loss for CTR prediction
   - Adam optimizer, learning rate 1e-3 to 1e-4

### Phase 6: Training Pipeline

1. Create `src/train.py` with:
   - Configurable hyperparameters via argparse or config file
   - Train/validation/test split (typically 8:1:1)
   - Training loop with epoch logging, it should be no more than 2 epoch
   - Early stopping on validation AUC
   - Model checkpoint saving to `artifacts/`
   - Metric logging to `runs/`

2. Create `src/evaluate.py` with:
   - Load saved model checkpoint
   - Compute metrics on test set: AUC, LogLoss, Accuracy
   - Generate comparison table across models
   - Save results to `reports/`

### Phase 7: Validation & Testing

1. **Syntax check**: Run `python -m py_compile` on all `.py` files
2. **Unit tests** in `tests/`:
   - Test model forward pass with dummy input
   - Test data loader output shapes
   - Test loss computation
   - Test metric calculation
3. **Smoke test**: Train for 1-2 epochs on a tiny data subset
   - Verify loss decreases
   - Verify AUC > 0.5 (better than random)
4. **Full validation**: Train on full dataset
   - Target: AUC close to paper's reported value
   - Compare against baselines
   - each experiment, train no more than 2 epoch

### Phase 8: Optimization

1. Hyperparameter tuning:
   - you should choose some of then, don't test all
   - Embedding dimension: try 8, 16, 32, 64
   - Hidden layer sizes: [256, 128], [512, 256, 128]
   - Learning rate: 1e-4 ~ 1e-7
   - Batch size: 512, 2048
   - Dropout rate: 0.1, 0.2, 0.3
   - L2 regularization: 1e-5, 1e-6

2. Architecture experiments:
   - Try different activation functions (ReLU, PReLU, GELU)
   - Vary number of cross layers / attention heads
   - Test with/without BatchNorm, LayerNorm

3. feature enginering experiments:
   - this is most importance
   - more cross features, for example age X gender, make a new feature, it is more sparse

4. Document each experiment in `runs/` with:
   - Config used
   - Train/val AUC curve
   - Best test AUC achieved

### Phase 9: Reporting

1. Generate final comparison report in `reports/final_report.md`:
   - Paper summary
   - Implementation details
   - Dataset description
   - Results table: Model | AUC | LogLoss | Parameters | Training Time
   - Analysis: where the implementation matches/differs from paper
   - Optimization findings
   - Conclusions and next steps

2. Archive model checkpoints in `artifacts/`

## Available tools

The following tools are preferred for this skill:

- `search_arxiv` — Search for papers on arXiv
- `get_arxiv_paper_details` — Get paper metadata
- `download_arxiv_pdf` — Download paper PDF
- `convert_pdf_to_markdown` — Parse PDF to markdown
- `convert_url_to_markdown` — Fetch and parse web content
- `read_file` — Read files from workspace
- `write_file` — Write files to workspace
- `append_file` — Append content to files
- `list_directory` — List workspace contents
- `create_directory` — Create directories
- `run_command` — Run shell commands (pip install, etc.)
- `run_python_script` — Execute Python scripts
- `run_bash_script` — Execute bash scripts
- `read_csv` — Read and inspect datasets
- `analyze_dataframe` — Statistical analysis of data
- `download_file` — Download datasets from URLs
- `search_in_files` — Search code files

Tool categories preferred:
- category: arxiv
- category: reader
- category: file
- category: bash
- category: data
- category: web

## Domain heuristics

### Common CTR model architectures

| Model | Year | Key Innovation |
|-------|------|---------------|
| LR | - | Linear baseline |
| FM | 2010 | 2nd-order feature interactions |
| DeepFM | 2017 | FM + DNN parallel |
| Wide&Deep | 2016 | Memorization + generalization |
| DCN | 2017 | Cross network for explicit interactions |
| DIN | 2018 | Attention on user behavior sequence |
| DIEN | 2019 | Interest evolution with GRU |
| AutoInt | 2019 | Multi-head self-attention on features |
| MMoE | 2018 | Multi-gate mixture of experts (multi-task) |
| PLE | 2020 | Progressive layered extraction (multi-task) |
| DLRM | 2019 | Facebook's recommendation model |
| DCN-v2 | 2021 | Improved cross network |

### Typical metric ranges (Criteo dataset)

- Logistic Regression: AUC ~0.785
- FM: AUC ~0.790
- DeepFM: AUC ~0.801
- DCN: AUC ~0.800
- DIN (with behavior): AUC ~0.805+
- State-of-the-art: AUC ~0.810-0.815

### Feature engineering conventions

- Categorical features: hash to fixed vocabulary, embed
- Numerical features: log-transform, bucketize, or normalize
- Cross features: handled by model (FM, cross-network)
- Sequence features (user behavior): variable-length, pad to max_len

## Constraints

- Dataset size must be < 1GB (sample if larger)
- All code must pass `python -m py_compile` without errors
- Unit tests must pass before full training
- Use PyTorch as the primary framework
- Keep training time reasonable (< 30 minutes on CPU for small datasets)
- All paths should be relative to the workspace directory
- training and testing should be full logs, and add auc and loss mark per 50 batchs, and draw training picture png show the auc and loss trends.

## Success criteria

- All code compiles without syntax errors
- Unit tests pass
- Model trains and loss decreases monotonically
- Test AUC > 0.5 (better than random)
- Target model AUC within 0.01-0.02 of paper's reported value (on same dataset)
- At least 2 baseline models implemented for comparison
- Final report generated with comparison table
