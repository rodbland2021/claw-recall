# Contributing to Claw Recall

Thanks for your interest in contributing! Whether it's a bug fix, new feature, documentation improvement, or just a question, all contributions are welcome.

## Ways to Contribute

### 🐛 Report Bugs
- Open an [issue](https://github.com/rodbland2021/claw-recall/issues) with a clear description
- Include: what you expected, what happened, steps to reproduce
- Logs and error messages help a lot

### 💡 Suggest Features
- Open an issue with the `enhancement` label
- Describe the use case, not just the solution
- Check existing issues first to avoid duplicates

### 🔧 Submit Code
1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Run the test suite: `python3 -m pytest tests/test_claw_recall.py -v`
5. Commit with a clear message
6. Open a pull request

### 📝 Improve Documentation
- README, inline comments, docstrings, examples
- Documentation PRs are always appreciated

### 🧪 Testing
- Run the test suite and report any failures
- Test on different setups (different agent counts, large databases)
- Add new test cases to `tests/test_claw_recall.py`

### 💬 Community
- Help others in [Discord](https://discord.gg/4wGTVa9Bt6)
- Answer questions in #support
- Share your setup in #show-and-tell

## Development Setup

```bash
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall
pip install -r requirements.txt
cp agents.json.example agents.json
# Edit agents.json for your setup
python3 -m claw_recall.indexing.watcher
```

## Code Style

- Python 3.10+
- Keep functions focused and documented
- Use type hints where practical
- No external dependencies unless necessary (stdlib + OpenAI + numpy for embeddings)

## Pull Request Guidelines

- One feature/fix per PR
- Include a clear description of what and why
- Tests must pass (`python3 -m pytest tests/test_claw_recall.py`)
- Update documentation if behaviour changes
- Update CHANGELOG.md under an `[Unreleased]` section

## Questions?

Open an issue or ask in [Discord #support](https://discord.gg/4wGTVa9Bt6).
