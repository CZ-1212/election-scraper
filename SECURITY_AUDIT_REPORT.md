# Security Audit Report
**Project:** California Election Data Scraper  
**Date:** November 5, 2025  
**Audited Files:** All Python source files in `src/`

---

## Executive Summary

**Overall Risk Level:** 🟡 **MEDIUM**

The codebase has **8 security issues** requiring attention:
- **🔴 HIGH SEVERITY:** 2 issues
- **🟠 MEDIUM SEVERITY:** 4 issues  
- **🟢 LOW SEVERITY:** 2 issues

---

## 🔴 HIGH SEVERITY ISSUES

### 1. Path Traversal Vulnerability (CWE-22)
**Files:** `src/clarity_scraper.py` (lines 564, 626)

**Issue:**
```python
# Line 564 - filename constructed from user-controllable report_type
filename = DATA_DIR / f"report_{timestamp}.{report_type}"

# Line 626 - output_file path not validated
output_file = DATA_DIR / f"scrape_{timestamp}.json"
```

**Risk:** An attacker controlling `report_type` could write files outside the intended directory using path traversal sequences like `../../../etc/passwd`.

**Recommendation:**
```python
# Validate file extension
ALLOWED_EXTENSIONS = {'xml', 'csv', 'xls', 'json'}
if report_type not in ALLOWED_EXTENSIONS:
    raise ValueError(f"Invalid file type: {report_type}")

# Sanitize filename to prevent path traversal
import os
filename = DATA_DIR / f"report_{timestamp}.{report_type}"
filename = filename.resolve()  # Resolve to absolute path
if not filename.is_relative_to(DATA_DIR.resolve()):
    raise ValueError("Path traversal attempt detected")
```

---

### 2. Arbitrary JavaScript Execution (CWE-94)
**Files:** `src/clarity_scraper.py` (line 103), `src/multi_platform_scraper.py` (lines 85-91)

**Issue:**
```python
# Executes arbitrary JavaScript in browser context
self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

# More complex JavaScript injection
stealth_script = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
...
"""
self.driver.execute_script(stealth_script)
```

**Risk:** While currently hardcoded, if this pattern is extended to accept user input, it could lead to arbitrary code execution in the browser context.

**Recommendation:**
```python
# Keep scripts as constants to prevent injection
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# Never accept user input for scripts
# Add documentation warning about JavaScript execution
```

---

## 🟠 MEDIUM SEVERITY ISSUES

### 3. No SSL Certificate Verification
**Files:** `src/clarity_scraper.py`, `src/multi_platform_scraper.py`

**Issue:**
```python
# No explicit SSL verification in requests
response = self.session.get(endpoint, headers={'Referer': self.url}, timeout=10)
```

**Risk:** Vulnerable to Man-in-the-Middle (MITM) attacks. Requests library verifies by default, but not explicitly enforced.

**Recommendation:**
```python
# Explicitly enforce SSL verification
session.verify = True  # Add to _create_session()

# For requests
response = self.session.get(
    endpoint,
    headers={'Referer': self.url},
    timeout=10,
    verify=True  # Explicit SSL verification
)
```

---

### 4. Unvalidated URL Input (CWE-918 - SSRF Risk)
**Files:** All scraper files

**Issue:**
```python
def __init__(self, url, reuse_driver=None, save_files=True):
    self.url = url  # No validation
    self.driver.get(self.url)  # Directly loads user-provided URL
```

**Risk:** If URLs come from untrusted sources, could be used for Server-Side Request Forgery (SSRF) attacks, accessing internal resources or unauthorized endpoints.

**Recommendation:**
```python
def _validate_url(self, url):
    """Validate URL against whitelist of allowed domains"""
    from urllib.parse import urlparse
    
    ALLOWED_DOMAINS = [
        'results.enr.clarityelections.com',
        'livevoterturnout.com',
        'sfelections.org',
        'santacruzcountyca.gov'
    ]
    
    parsed = urlparse(url)
    
    # Check scheme
    if parsed.scheme not in ['http', 'https']:
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")
    
    # Check domain
    if not any(domain in parsed.netloc for domain in ALLOWED_DOMAINS):
        raise ValueError(f"URL domain not allowed: {parsed.netloc}")
    
    # Prevent localhost/internal IPs
    if parsed.netloc in ['localhost', '127.0.0.1', '0.0.0.0']:
        raise ValueError("Internal URLs not allowed")
    
    return url

def __init__(self, url, reuse_driver=None, save_files=True):
    self.url = self._validate_url(url)  # Validate before use
```

---

### 5. Insecure Temporary File Creation
**Files:** `src/clarity_scraper.py` (line 564)

**Issue:**
```python
# Predictable filename with timestamp
filename = DATA_DIR / f"report_{timestamp}.{report_type}"
```

**Risk:** Predictable filenames could lead to race conditions or file overwrite attacks.

**Recommendation:**
```python
import secrets
import tempfile

# Use secure random filename generation
random_suffix = secrets.token_hex(8)
filename = DATA_DIR / f"report_{timestamp}_{random_suffix}.{report_type}"

# Or use Python's tempfile
with tempfile.NamedTemporaryFile(
    mode='wb',
    suffix=f'.{report_type}',
    dir=DATA_DIR,
    delete=False
) as f:
    f.write(response.content)
    filename = f.name
```

---

### 6. No Rate Limiting / DoS Protection
**Files:** All scraper files

**Issue:**
```python
# No rate limiting on scraping requests
# Could overwhelm target servers
self.driver.get(self.url)
```

**Risk:** Could be used to perform Denial of Service (DoS) attacks on election websites. Also violates responsible scraping practices.

**Recommendation:**
```python
import time
from functools import wraps

class RateLimiter:
    def __init__(self, min_interval=2.0):
        self.min_interval = min_interval
        self.last_request = 0
    
    def wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()

# Add to scraper classes
self.rate_limiter = RateLimiter(min_interval=2.0)

def scrape(self):
    self.rate_limiter.wait()  # Enforce minimum delay
    # ... rest of scraping code
```

---

## 🟢 LOW SEVERITY ISSUES

### 7. Hardcoded Credentials/Paths (Information Disclosure)
**Files:** `src/clarity_scraper.py` (line 26)

**Issue:**
```python
DATA_DIR = Path("election_data")  # Hardcoded path
TARGET_URL = "https://..."  # Hardcoded URL exposed in code
```

**Risk:** Minor information disclosure. Paths and URLs visible in source code.

**Recommendation:**
```python
# Use environment variables for configuration
import os
from pathlib import Path

DATA_DIR = Path(os.getenv('ELECTION_DATA_DIR', 'data'))
TARGET_URL = os.getenv('TARGET_URL', 'https://...')
```

---

### 8. Insufficient Error Information (CWE-209)
**Files:** All files with exception handling

**Issue:**
```python
except Exception as e:
    print(f"Error: {e}")  # May expose sensitive information
    import traceback
    print(f"Traceback: {traceback.format_exc()}")  # Full stack traces exposed
```

**Risk:** Detailed error messages and stack traces could reveal sensitive information about the system or code structure.

**Recommendation:**
```python
import logging

# Use logging with appropriate levels
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    # ... code ...
except Exception as e:
    # Log full details for debugging (to file)
    logger.exception("Scraping error occurred")
    
    # Show minimal info to user
    print(f"⚠️  Scraping failed. Check logs for details.")
    
    # Never expose sensitive details in production
```

---

## Additional Security Recommendations

### 9. Dependency Security
**File:** `requirements.txt`

**Issue:**
```
selenium==4.15.2
requests==2.31.0
beautifulsoup4==4.12.2
schedule==1.2.0
```

**Recommendation:**
```bash
# Add dependency vulnerability scanning
pip install safety
safety check

# Use pip-audit
pip install pip-audit
pip-audit

# Add to CI/CD pipeline
```

---

### 10. Chrome Driver Security
**Files:** All files using Selenium

**Issue:**
```python
options.add_argument('--no-sandbox')  # Disables security sandbox
options.add_argument('--disable-dev-shm-usage')
```

**Risk:** `--no-sandbox` disables Chrome's security sandbox, making the browser vulnerable to exploits.

**Recommendation:**
```python
# Only use --no-sandbox in containerized environments
import os

options = webdriver.ChromeOptions()
options.add_argument('--headless')

# Only disable sandbox in Docker/containers
if os.getenv('DOCKER_CONTAINER'):
    options.add_argument('--no-sandbox')
else:
    # Keep sandbox enabled for security
    pass
```

---

## Security Best Practices Checklist

### ✅ Already Implemented
- [x] Using HTTPS for all external requests
- [x] Headless browser operation
- [x] Connection pooling with retry strategy
- [x] Timeout configurations
- [x] User-Agent headers (transparency)
- [x] Basic error handling

### ⚠️ Needs Implementation
- [ ] Input validation for URLs and file paths
- [ ] SSL certificate verification (explicit)
- [ ] Rate limiting per domain
- [ ] Secure file creation
- [ ] Logging instead of print statements
- [ ] Environment-based configuration
- [ ] Dependency vulnerability scanning
- [ ] Path traversal prevention
- [ ] Domain whitelist enforcement
- [ ] Secure Chrome options (sandbox)

---

## Compliance & Legal Considerations

### robots.txt Compliance
**Status:** ⚠️ **NOT CHECKED**

**Issue:** The scraper does not check `robots.txt` before scraping.

**Recommendation:**
```python
from urllib.robotparser import RobotFileParser

def check_robots_txt(url):
    """Check if scraping is allowed per robots.txt"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return rp.can_fetch("*", url)
    except:
        # If robots.txt doesn't exist, assume allowed
        return True

# Use before scraping
if not check_robots_txt(self.url):
    raise ValueError("Scraping not allowed per robots.txt")
```

---

## Priority Action Items

### 🔴 CRITICAL (Fix Immediately)
1. **Add URL validation** to prevent SSRF attacks
2. **Add path traversal protection** for file operations
3. **Implement rate limiting** to prevent abuse

### 🟠 HIGH (Fix Soon)
4. **Explicit SSL verification** for all requests
5. **Secure file creation** with random names
6. **Remove `--no-sandbox` flag** (or document risks)

### 🟢 MEDIUM (Plan to Fix)
7. **Add logging framework** (replace print statements)
8. **Environment-based configuration**
9. **Dependency security scanning** in CI/CD
10. **robots.txt compliance checking**

---

## Security Testing Recommendations

### Manual Testing
- [ ] Test with malicious URLs (SSRF attempts)
- [ ] Test with path traversal filenames
- [ ] Test rate limiting under load
- [ ] Verify SSL certificate validation

### Automated Testing
```bash
# Static analysis
pip install bandit
bandit -r src/

# Dependency scanning
pip install safety
safety check

# Code quality
pip install pylint
pylint src/
```

---

## Conclusion

The codebase is **functional but requires security hardening** before production use, especially if:
- URLs come from untrusted sources
- The tool is deployed in shared/public environments
- The tool is used in automated/scheduled modes

**Estimated Effort to Fix:** 4-8 hours for critical and high-priority issues.

**Risk if Unaddressed:** Potential for SSRF attacks, path traversal exploits, and abuse of target websites.

---

**Report Generated:** November 5, 2025  
**Next Review:** After implementing critical fixes

