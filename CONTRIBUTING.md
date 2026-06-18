# Contributing to BlackKnightController

We welcome community contributions! To maintain code quality and security standards, please follow this workflow.

## Contribution Workflow

### 1. Create a Feature Branch
Always branch from `main` for new features, bug fixes, or improvements:

```bash
git checkout -b feature/your-feature-name
# or for bug fixes:
git checkout -b bugfix/issue-description
# or for documentation:
git checkout -b docs/what-youre-documenting
```

### 2. Make Your Changes
Follow these guidelines when developing:

- **Test thoroughly** - Ensure your changes don't break existing functionality
- **Keep commits atomic** - Logical, focused commits are easier to review
- **Update documentation** - If your changes affect user-facing features, update the README
- **Follow code style** - Match the existing codebase conventions

### 3. Security & Secrets Compliance

**CRITICAL: Never commit secrets, tokens, passwords, or API keys in any form.**

#### Encrypted Secret Storage
BlackKnightController uses encryption-at-rest for sensitive values:

- All secret fields (`password`, `controller_password`, `token_value`) **must be encrypted** before storage
- Encryption uses per-install SALT stored in `dictionaries/secrets_meta.json`
- Master key stored in `keys/bkc_master_key` (never commit this file)
- Use `python3 bkc_cli.py migrate-secrets` to encrypt plaintext values

#### What You CANNOT Commit
❌ Base64-encoded credentials (easily reversible)  
❌ Plaintext passwords or tokens in JSON files  
❌ API keys or Bearer tokens in any form  
❌ SSH private keys  
❌ Database connection strings with credentials  
❌ Any file in `keys/` directory  

#### Proper Token/Password Handling
✅ All credentials must be **one-way hashed** (bcrypt, Argon2, PBKDF2)  
✅ Follow existing password/token encryption models in the codebase  
✅ Use `dictionaries/secrets_meta.json` for salt management  
✅ Test credential handling without actual secrets  

#### If You Accidentally Committed Secrets
1. **Do not push** - catch it before the PR
2. **Rotate the credentials immediately** - assume they're compromised
3. **Rewrite history** if not yet pushed: `git reset HEAD~1`, remove the secret, re-commit
4. **If already pushed** - notify maintainers immediately and file a security disclosure

### 4. Submit a Pull Request

When your feature is ready:

```bash
git push origin feature/your-feature-name
```

Then on GitHub:
- Create a Pull Request from your branch to `main`
- Fill out the PR template completely
- Reference any related issues: `Closes #123`
- Describe what changed and why
- List any testing performed

#### PR Requirements Before Merge
- ✅ All tests pass
- ✅ No secrets or sensitive data in commits
- ✅ Code review approval (at least one maintainer)
- ✅ Linting/style checks pass
- ✅ Documentation updated if needed
- ✅ Security review for credential handling (if applicable)

### 5. Code Review & Validation

Your PR will be reviewed for:
- **Functionality** - Does it work as intended?
- **Code Quality** - Is it maintainable and performant?
- **Security** - No secrets, proper encryption, safe API usage
- **Testing** - Adequate test coverage for new features
- **Documentation** - Clear, accurate, and complete

**Note:** We will not merge broken code or PRs that violate security standards. Plan for iteration and feedback.

## Alpha Status & UI Development

BlackKnightController is currently in **alpha**. The UI needs significant work, and we're actively seeking contributions in:

- **Frontend/UI improvements** - Better UX, modern design patterns
- **User workflow optimization** - Making common tasks faster and easier
- **Testing & validation** - Comprehensive test coverage
- **Documentation** - API docs, tutorials, example configurations
- **Security hardening** - Penetration testing, vulnerability identification

## Merge to Main

Once your PR is approved and validated:
1. Code review is complete
2. All CI/CD checks pass
3. Maintainer approves the merge
4. Your branch is deleted post-merge

## Questions?

- Check existing issues and discussions first
- Open an issue for bugs or feature requests
- Review the README for architecture and usage detail
- Ask in comments on related PRs/issues

Thank you for contributing to BlackKnightController! 🚀
