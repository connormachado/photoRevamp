# ============================================================
#  photo memory — developer shortcuts
#  Usage: make <target>
# ============================================================

# ── paths ────────────────────────────────────────────────────
BACKEND_DIR  := backend
FRONTEND_DIR := photo-search
VENV_DIR     := .venvIni
PYTHON 		 := arch -arm64 .venv/bin/python3

# ── colours (purely cosmetic) ───────────────────────────────
BOLD  := \033[1m
RESET := \033[0m
GREEN := \033[32m
CYAN  := \033[36m

# ============================================================
.PHONY: help start stop install install-backend install-frontend embed clean

# ── default target ──────────────────────────────────────────
.DEFAULT_GOAL := help

help: ## Show this help message
	@echo ""
	@echo "  $(BOLD)photo revamp$(RESET) — available commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2}'
	@echo ""

# ============================================================
#  START / STOP
# ============================================================

start: ## Start backend (Flask) + frontend (Vite) in one terminal
	@echo ""
	@echo "  $(BOLD)$(GREEN)Starting photo memory…$(RESET)"
	@echo "  Backend  → http://localhost:5001"
	@echo "  Frontend → http://localhost:5173"
	@echo ""
	@echo "  Press Ctrl-C once to stop both servers."
	@echo ""
	@trap 'kill %1 %2 2>/dev/null; echo "\n  Servers stopped."; exit 0' INT; \
		$(PYTHON) $(BACKEND_DIR)/server.py & \
		(cd $(FRONTEND_DIR) && npm run dev) & \
		wait

stop: ## Kill any processes on ports 5001 and 5173
	@echo "  Stopping servers on :5001 and :5173…"
	@-lsof -ti :5001 | xargs kill -9 2>/dev/null || true
	@-lsof -ti :5173 | xargs kill -9 2>/dev/null || true
	@echo "  Done."

# ============================================================
#  INSTALL
# ============================================================

install: install-backend install-frontend ## Install all dependencies (Python + Node)

install-backend: ## Create venv and install Python dependencies
	@echo "  $(BOLD)Installing Python dependencies inside virtual environment…$(RESET)"
	/user/local/bin/python3.12 -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --upgrade pip
	$(VENV_DIR)/bin/pip install -r requirements.txt
	@echo "  $(GREEN)Backend ready!$(RESET)"

install-frontend: ## Install Node dependencies
	@echo "  $(BOLD)Installing Node dependencies…$(RESET)"
	cd $(FRONTEND_DIR) && npm install
	@echo "  $(GREEN)Frontend ready!$(RESET)"

# ============================================================
#  UTILITIES
# ============================================================

# embed: ## Run the photo indexing pipeline
# 	@echo "  $(BOLD)Running embed_photos.py…$(RESET)"
# 	$(PYTHON) $(BACKEND_DIR)/embed_photos.py

clean: ## Remove Python cache files
	@echo "  Cleaning up __pycache__ and .pyc files…"
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "  Done."