## Summary

Describe the change and the affected public packages.

## Testing

- [ ] Backend tests pass when backend code changed
- [ ] Frontend tests/typecheck pass when frontend code changed
- [ ] Python wheel/sdist or npm package contents were checked
- [ ] Frozen verifier fixtures cover every changed transaction shape
- [ ] Reference consumer was checked when the shared contract changed

## Contract checklist

- [ ] Public API, HTTP and payment-state changes are documented
- [ ] English and Russian documentation/copy remain complete
- [ ] Consumer-owned migrations and CommerceXL compatibility were considered
- [ ] Orcestr Auth ownership/CSRF boundaries were considered
- [ ] Local and published dependency modes remain separate

## Payment security

- [ ] Exact cluster, program, mint, recipient, raw amount and reference are verified
- [ ] Legacy Token Program remains explicitly rejected
- [ ] RPC unknown/outage does not become payment success or permanent failure
- [ ] Duplicate, concurrent, cancelled and late payment behavior is tested
- [ ] No credentials, capabilities, private keys, signed transactions or production data are included
