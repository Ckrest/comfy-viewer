# Pre-Release Checklist

Things to verify/update before publishing to GitHub.

## Placeholders to Replace

- [ ] **README.md line 36**: `YOUR_USERNAME` in git clone URL
- [ ] **CONDUIT.md**: Verify GitHub URL is correct (`NickPittas/ComfyUI-Conduit`)

## Files to Verify Are Gitignored

These should NOT appear in git status after a fresh clone:
- [ ] `registrations.db` (user data)
- [ ] `hooks/*.py` except `__init__.py` and `_default.py`
- [ ] `subscribers/*.py` except `__init__.py`
- [ ] `.system/` (local registry metadata)

## Code Quality

- [ ] No hardcoded paths (grep for `/home/nick`)
- [ ] No debug print statements
- [ ] No commented-out code blocks
- [ ] All imports resolve (run `python -c "import app"`)

## Documentation

- [ ] README has accurate feature list
- [ ] CONDUIT.md setup instructions work
- [ ] LICENSE file exists
- [ ] requirements.txt is complete

## Testing

- [ ] Fresh clone + setup works:
  ```bash
  git clone <repo>
  cd comfy-viewer
  python -m venv venv && source venv/bin/activate
  pip install -r requirements.txt
  cp settings.yaml.example ~/.config/comfy-viewer/config.yaml
  ./comfy-viewer
  ```
- [ ] Viewer page loads (`/`)
- [ ] Library page loads (`/library`)
- [ ] File watching detects new images
- [ ] Thumbnails generate correctly

## Optional: With Conduit

- [ ] Workflow list populates
- [ ] Workflow execution works
- [ ] Images appear after generation

---

## Personal Setup (Not for GitHub)

After cloning for personal use, remember to:

1. Copy `settings.yaml.example` to `~/.config/comfy-viewer/config.yaml` and configure paths
2. Add custom hooks to `hooks/` folder
3. Add custom subscribers to `subscribers/` folder (e.g., redis_subscriber.py)
