## S3 Support for Solidity Compilers

This project has been enhanced with S3 support for hosting Solidity compilers, allowing for better performance and reliability compared to downloading from the official repository each time.

### sync_solc_to_s3.py Script

The `sync_solc_to_s3.py` script synchronizes Solidity compilers from the official Ethereum repository to an S3 bucket. This script:

- Downloads all available Linux AMD64 Solidity compiler versions from `https://solc-bin.ethereum.org/linux-amd64/`
- Uploads them to S3 with the required directory structure for the smart-contract-verifier
- Generates SHA256 hash files for integrity verification
- Supports concurrent downloads/uploads for better performance
- Skips already existing versions to avoid unnecessary re-uploads

#### Usage

```bash
# Sync all versions from official repository
python3 sync_solc_to_s3.py

# Sync with custom parameters
python3 sync_solc_to_s3.py --limit 10 --workers 5 --bucket your-bucket-name

# Sync local compilers
python3 sync_solc_to_s3.py --local-dir /Users/will9709/code/my_contract/solc_compiler

```

#### Local Compiler Support

The script supports uploading local Solidity compilers to S3. It automatically detects version information by executing `solc --version` on each compiler binary found. The script can scan:

- Direct `solc` files in the specified directory
- Any files starting with `solc` (e.g., `solc-0.8.19`, `solc_old`)
- Subdirectories containing `solc` files

Version detection automatically extracts the correct format (e.g., `v0.4.10+commit.9e8cc01b`) from the compiler's output, ensuring proper S3 directory structure.

#### Environment Variables

- `AWS_ACCESS_KEY_ID`: S3 access key
- `AWS_SECRET_ACCESS_KEY`: S3 secret key  
- `AWS_REGION`: S3 region (default: us-east-1)
- `S3_BUCKET`: S3 bucket name (default: seismic-solidity)

### S3 Configuration

The S3 support is configured in `smart-contract-verifier-server/config/base.toml`:

```toml
[solidity.fetcher]
s3 = { 
    access_key = "YOUR_ACCESS_KEY", 
    secret_key = "YOUR_SECRET_KEY", 
    region = "us-east-1", 
    bucket = "solidity-public" 
}
```

This replaces the default list fetcher that downloads from the official repository, providing faster and more reliable access to Solidity compilers.

### Running the service locally

To start the smart-contract-verifier-server with debug logging:

```bash
RUST_LOG=debug SMART_CONTRACT_VERIFIER__CONFIG=./smart-contract-verifier-server/config/base.toml cargo run --bin smart-contract-verifier-server
```

The service will start on port 8050 by default.

### API Verification

After starting the service, you can verify it's working correctly using these endpoints:

#### 1. Check Available Solidity Versions
```bash
curl http://localhost:8050/api/v2/verifier/solidity/versions
```
This endpoint should return a JSON list of available Solidity compiler versions from your S3 bucket.

#### 2. Verify Smart Contract
```bash
curl --location 'http://localhost:8050/api/v2/verifier/solidity/sources:verify-standard-json' \
--header 'Content-Type: application/json' \
--data '{
  "bytecode": "0x6080604052348015600e575f5ffd5b5060e78061001b5f395ff3fe6080604052348015600e575f5ffd5b50600436106026575f3560e01c80639c66a64a14602a575b5f5ffd5b603960353660046051565b603b565b005b805f5f8282b0604991906067565b9091b1505050565b5f602082840312156060575f5ffd5b5035919050565b80820180821115608557634e487b7160e01b5f52601160045260245ffd5b9291505056fea2646970667358221220e74dbdb94c2a0f1c677090c872c617447dff5192bf0713f4cfc753a2c75db5eb64736f6c637828302e382e32392d646576656c6f702e323032352e392e31352b636f6d6d69742e64346238633761650059",
  "bytecodeType": "CREATION_INPUT",
  "compilerVersion": "v0.8.29+commit.d4b8c7ae",
  "input": "{\"language\":\"Solidity\",\"sources\":{\"test_example.sol\":{\"content\":\"// SPDX-License-Identifier: MIT\\npragma solidity ^0.8.0;\\n\\ncontract ShieldedWallet {\\n    suint256 private balance;\\n    \\n    function addFunds(suint256 amount) external {\\n        balance += amount;\\n    }\\n}\"}},\"settings\":{\"optimizer\":{\"enabled\":true,\"runs\":200},\"outputSelection\":{\"*\":{\"*\":[\"abi\",\"evm.bytecode\",\"evm.deployedBytecode\",\"metadata\"]}}}}"
}'
```
This endpoint performs smart contract verification using the specified compiler version. A successful response should return `"status": "SUCCESS"` along with contract details including ABI, source code, and compilation artifacts.

#### 3. Health Check
```bash
curl http://localhost:8050/health
```
This endpoint verifies the service is running and responding properly.

*Based on: https://github.com/blockscout/blockscout-rs/blob/main/smart-contract-verifier/README.md*

