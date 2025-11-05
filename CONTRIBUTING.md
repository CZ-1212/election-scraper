# Contributing to California Election Data Scraper

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## How to Contribute

### Reporting Issues

If you find a bug or have a suggestion:
1. Check if the issue already exists in the GitHub issues
2. If not, create a new issue with:
   - Clear description of the problem or suggestion
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Your environment (OS, Python version, etc.)

### Adding Support for New Counties

To add a new county:

1. **Identify the Platform**
   - Determine which platform the county uses (Clarity, LiveVoterTurnout, custom)
   - Test the website manually to understand the data structure

2. **Create or Modify Scraper**
   - For Clarity sites: Add to `CLARITY_SITES` in `src/test_clarity_only.py`
   - For other platforms: Create a new scraper class in `src/multi_platform_scraper.py`

3. **Test Thoroughly**
   - Test with the actual website
   - Verify voter turnout data is extracted correctly
   - Verify contest data is complete
   - Test error handling

4. **Update Documentation**
   - Add the county to README.md
   - Document any platform-specific quirks
   - Update sample data if needed

### Code Style

- Follow PEP 8 guidelines
- Use meaningful variable names
- Add comments for complex logic
- Include docstrings for classes and functions

### Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-county-support`)
3. Make your changes
4. Test thoroughly
5. Commit with clear messages
6. Push to your fork
7. Create a Pull Request with:
   - Description of changes
   - Which counties/platforms are affected
   - Test results

### Development Setup

```bash
# Clone your fork
git clone https://github.com/yourusername/election-scraper.git
cd election-scraper

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python3 src/scrape_3_working.py
python3 src/test_clarity_only.py
```

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Follow the project's goals and vision

## Questions?

If you have questions about contributing, feel free to:
- Open an issue for discussion
- Check existing documentation in the `docs/` folder
- Review closed issues for similar questions

Thank you for contributing to making election data more accessible!

