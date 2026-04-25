"""Tests for the URL whitelist and secure-filepath helpers.

Both src/clarity_scraper.py and src/multi_platform_scraper.py define their
own copies of validate_url and validate_and_secure_filepath. They are
tested against the same expectations to catch drift between the two.
"""

import pytest

from src import clarity_scraper as cs
from src import multi_platform_scraper as mps


SECURITY_MODULES = [cs, mps]


@pytest.mark.parametrize("module", SECURITY_MODULES)
class TestValidateUrl:
    def test_accepts_whitelisted_domain(self, module):
        url = "https://results.enr.clarityelections.com/CA/Marin/124182/web.345435/"
        assert module.validate_url(url) == url

    def test_accepts_subdomain_of_whitelisted(self, module):
        url = "https://foo.clarityelections.com/x"
        assert module.validate_url(url) == url

    def test_rejects_unknown_domain(self, module):
        with pytest.raises(ValueError, match="not in allowed list"):
            module.validate_url("https://evil.example.com/x")

    def test_rejects_localhost(self, module):
        with pytest.raises(ValueError):
            module.validate_url("http://localhost/admin")

    def test_rejects_loopback_ip(self, module):
        with pytest.raises(ValueError):
            module.validate_url("http://127.0.0.1/admin")

    def test_rejects_non_http_scheme(self, module):
        with pytest.raises(ValueError, match="scheme"):
            module.validate_url("file:///etc/passwd")

    def test_rejects_lookalike_domain(self, module):
        # netloc must equal allowed_domain or end with "." + allowed_domain.
        # "evilclarityelections.com" must NOT match "clarityelections.com".
        with pytest.raises(ValueError):
            module.validate_url("https://evilclarityelections.com/x")


@pytest.mark.parametrize("module", SECURITY_MODULES)
class TestValidateAndSecureFilepath:
    def test_returns_path_inside_base_dir(self, tmp_path, module):
        result = module.validate_and_secure_filepath(tmp_path, "report", "csv")
        assert result.is_relative_to(tmp_path.resolve())
        assert result.suffix == ".csv"

    def test_filename_includes_random_suffix(self, tmp_path, module):
        a = module.validate_and_secure_filepath(tmp_path, "report", "csv")
        b = module.validate_and_secure_filepath(tmp_path, "report", "csv")
        assert a != b, "secrets-derived suffix should make filenames unique"

    def test_rejects_disallowed_extension(self, tmp_path, module):
        with pytest.raises(ValueError, match="extension"):
            module.validate_and_secure_filepath(tmp_path, "report", "exe")

    def test_accepts_each_allowed_extension(self, tmp_path, module):
        for ext in module.ALLOWED_FILE_EXTENSIONS:
            module.validate_and_secure_filepath(tmp_path, "report", ext)


def test_clarity_whitelist_is_subset_of_multi_platform():
    """clarity_scraper handles a narrower set of domains than
    multi_platform_scraper, but every domain it permits must also be
    permitted by the broader scraper. Drift in the other direction
    means a Clarity-only domain was added without being shared, and is
    almost always a copy-paste oversight."""
    assert set(cs.ALLOWED_DOMAINS).issubset(set(mps.ALLOWED_DOMAINS))


def test_clarity_whitelist_includes_clarity_domains():
    for required in ("clarityelections.com", "results.enr.clarityelections.com"):
        assert required in cs.ALLOWED_DOMAINS


def test_two_modules_share_allowed_extensions():
    assert cs.ALLOWED_FILE_EXTENSIONS == mps.ALLOWED_FILE_EXTENSIONS
