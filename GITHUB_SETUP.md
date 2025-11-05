# GitHub Setup Guide

This guide will help you push your organized codebase to GitHub.

## Step 1: Create a New Repository on GitHub

1. Go to [GitHub](https://github.com) and sign in
2. Click the **+** button in the top right corner
3. Select **New repository**
4. Fill in the repository details:
   - **Repository name**: `california-election-scraper` (or your preferred name)
   - **Description**: "A robust Python-based election night scraper for California counties"
   - **Visibility**: Choose Public or Private
   - **DO NOT** initialize with README, .gitignore, or license (we already have these)
5. Click **Create repository**

## Step 2: Configure Git (If Not Already Done)

```bash
# Set your name and email
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Verify settings
git config --global user.name
git config --global user.email
```

## Step 3: Add Remote and Push

Replace `yourusername` with your actual GitHub username:

```bash
# Add the remote repository
git remote add origin https://github.com/yourusername/california-election-scraper.git

# Verify remote was added
git remote -v

# Push to GitHub
git push -u origin main
```

If you prefer SSH (recommended for frequent use):

```bash
# Add remote using SSH
git remote add origin git@github.com:yourusername/california-election-scraper.git

# Push to GitHub
git push -u origin main
```

## Step 4: Verify on GitHub

1. Refresh your GitHub repository page
2. You should see all your files organized properly:
   - ✅ Source code in `src/`
   - ✅ Documentation in `docs/`
   - ✅ Sample data in `data/samples/`
   - ✅ README.md displayed on the main page
   - ✅ LICENSE file
   - ✅ .gitignore properly ignoring files

## Step 5: Update Repository URL in setup.py (Optional)

Once you know your repository URL, update it in `setup.py`:

```python
url="https://github.com/yourusername/california-election-scraper",
```

Then commit and push the change:

```bash
git add setup.py
git commit -m "Update repository URL in setup.py"
git push
```

## Step 6: Configure GitHub Settings (Optional)

1. **Add Topics**: Go to your repository → Click ⚙️ (gear) next to About
   - Add topics: `election-data`, `web-scraping`, `california`, `selenium`, `python`

2. **Enable Issues**: Settings → General → Features → Check "Issues"

3. **Add Description**: Settings → General → Description
   - "A robust Python-based election night scraper for California counties"

4. **Set Homepage**: (optional) Add your project website or documentation URL

## Troubleshooting

### Authentication Required
If GitHub asks for authentication:
- **HTTPS**: Use your GitHub username and a [Personal Access Token](https://github.com/settings/tokens)
- **SSH**: Set up [SSH keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)

### Permission Denied
If you get "permission denied":
```bash
# Check your remote URL
git remote -v

# If using HTTPS, make sure your token has correct permissions
# If using SSH, make sure your SSH key is added to GitHub
```

### Remote Already Exists
If you get "remote origin already exists":
```bash
# Remove the old remote
git remote remove origin

# Add the new one
git remote add origin https://github.com/yourusername/california-election-scraper.git
```

## Next Steps

After pushing to GitHub:

1. ✅ Add a nice repository banner/logo (optional)
2. ✅ Star your own repository
3. ✅ Share with collaborators
4. ✅ Set up GitHub Actions for CI/CD (optional)
5. ✅ Enable GitHub Pages for documentation (optional)

## Repository Structure Summary

Your repository is now organized as:
```
california-election-scraper/
├── .github/              # GitHub templates and workflows
├── src/                  # Python source code
├── docs/                 # Documentation files
├── data/                 # Data directory (scraped data ignored)
├── README.md            # Main documentation
├── LICENSE              # MIT License
├── requirements.txt     # Python dependencies
├── setup.py            # Package installation
└── CONTRIBUTING.md     # Contribution guidelines
```

## Support

If you encounter issues:
- Check [GitHub Docs](https://docs.github.com)
- Review git error messages carefully
- Make sure you have the correct repository permissions

Happy coding! 🚀

