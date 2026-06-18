# Security Policy

## Reporting Security Vulnerabilities

**Please do not open public GitHub issues for security vulnerabilities.**

If you discover a security issue in BlackKnightController, email security details to the maintainer directly. Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if available)

We'll acknowledge receipt within 48 hours and work on a fix promptly.

## Secret Storage & Encryption

### Overview
BlackKnightController implements encryption-at-rest for sensitive data to prevent accidental exposure of credentials.

### Architecture

#### Master Key Management
- **Location**: `keys/bkc_master_key` (DO NOT commit to repository)
- **Alternative**: Set `BKC_MASTER_SECRET` environment variable
- **Backup**: Store outside the repository in secure location
- **Rotation**: Document your key rotation process separately

#### Salt Storage
- **Location**: `dictionaries/secrets_meta.json`
- **Purpose**: Per-installation salt for encryption
- **Backup**: Must be backed up alongside master key for decryption recovery
- **Note**: Compromised master key + salt = compromised secrets

### Encrypted Fields

The following fields are automatically encrypted before storage:
- `password`
- `controller_password`
- `token_value`
- Any custom credential fields following this pattern

### Encryption Standards

- **Algorithm**: Industry-standard encryption (AES-256 recommended)
- **Mode**: Authenticated encryption (GCM)
- **Key Derivation**: PBKDF2 or similar (minimum 100,000 iterations)
- **Salt**: Cryptographically random, minimum 16 bytes

### Migration from Plaintext

If you have existing plaintext secrets in your deployment:

```bash
python3 bkc_cli.py migrate-secrets
```

This command:
1. Reads plaintext secret values from `dictionaries/*.json`
2. Encrypts them using your master key
3. Replaces plaintext values with encrypted ciphertext
4. Stores salt in `dictionaries/secrets_meta.json`
5. Creates backup of original (if applicable)

**Run this once and verify no plaintext remains.**

### What NOT to Commit

❌ **Base64-encoded secrets** - Trivially reversible  
❌ **Plaintext passwords or tokens** - Will be caught in code review  
❌ **API keys in configuration** - Use environment variables instead  
❌ **SSH private keys** - Use SSH key agents  
❌ **Any file in `keys/` directory** - Add to `.gitignore`  
❌ **Hardcoded credentials anywhere** - Automatic rejection  

### Token & Password Best Practices

#### For Storage
- **One-way hashing**: Use bcrypt (recommended), Argon2, or PBKDF2
- **Never reversible**: Hashed passwords cannot be decrypted
- **Salting**: Each password gets unique salt (automatic in bcrypt)
- **Comparison**: Use constant-time comparison to prevent timing attacks

#### For Transmission
- **HTTPS only** - Never send credentials over unencrypted channels
- **Short-lived tokens** - Implement token expiration
- **Token revocation** - Support immediate token invalidation
- **Secure headers**: Use `Secure`, `HttpOnly`, `SameSite` for session cookies

#### For Audit
- **Log redaction**: Never log plaintext credentials
- **Audit trail**: Track when credentials are accessed/rotated
- **Alerts**: Monitor for unusual credential access patterns

### Recovery Procedure

If your BKC installation is damaged but the following survive:
- `keys/bkc_master_key`
- `dictionaries/secrets_meta.json`

You can fully recover all stored secrets:

```bash
# BKC will automatically decrypt using the master key and salt
python3 bkc_cli.py list-secrets  # Shows decrypted values (use with care!)
```

**Critical**: Restore these files in a secure environment with restricted access.

### Key Rotation

Currently, BKC does not support key rotation without manual intervention. To rotate:

1. Decrypt all secrets using current key
2. Change master key in `keys/bkc_master_key` or `BKC_MASTER_SECRET`
3. Re-encrypt all secrets
4. Update `dictionaries/secrets_meta.json` with new salt
5. Verify all secrets still decrypt correctly
6. Destroy old key securely (overwrite, don't just delete)

Plan your key rotation strategy **before** deploying to production.

### Known Limitations

- **Master key storage**: Currently no hardware security module (HSM) integration
- **Key rotation**: Manual process, planned for future automation
- **Audit logging**: Basic logging only, consider external SIEM for compliance
- **Multi-tenant**: Single master key per installation, not multi-tenant compatible

### Future Security Enhancements

Planned improvements:
- [ ] Hardware Security Module (HSM) support
- [ ] Automated key rotation
- [ ] Field-level audit logging
- [ ] Rate limiting on secret access
- [ ] Integration with secret management systems (Vault, AWS Secrets Manager, etc.)

## Contribution Security Requirements

All contributions must meet these security standards:

1. **No plaintext secrets** - Automatic PR rejection
2. **Proper encryption** - Use provided encryption utilities
3. **Input validation** - Sanitize all external inputs
4. **Dependency review** - Report any security issues in dependencies
5. **Secure defaults** - Cryptographic operations must be secure by default

See `CONTRIBUTING.md` for detailed security submission guidelines.

## Dependency Security

We track security advisories for all dependencies. To report a vulnerable dependency:

1. Check [GitHub Security Advisories](https://github.com/advisories)
2. Open an issue if we're using a vulnerable version
3. Include the CVE/advisory link and recommended fix

## Security Roadmap

- Implement automated secret scanning in CI/CD
- Add pre-commit hooks to catch hardcoded secrets
- Support for external secret stores (HashiCorp Vault, AWS Secrets Manager)
- Hardware security module (HSM) integration
- Regular security audits and penetration testing

## References

- [OWASP: Secrets Management](https://owasp.org/www-community/Sensitive_Data_Exposure)
- [NIST: Password Storage](https://pages.nist.gov/800-63-3/sp800-63b.html)
- [CWE-798: Use of Hard-Coded Credentials](https://cwe.mitre.org/data/definitions/798.html)
- [bcrypt: Secure Password Hashing](https://en.wikipedia.org/wiki/Bcrypt)
