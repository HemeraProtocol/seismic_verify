use foundry_compilers_new::{artifacts, solc::SolcLanguage};
use std::{collections::BTreeMap, path::Path};

#[allow(dead_code)]
pub async fn test_async_compile_output() -> Result<(), Box<dyn std::error::Error>> {
    let compiler_path = Path::new("/tmp/solidity-compilers/v0.8.29+commit.d4b8c7ae/solc");
    // let compiler_path = Path::new("/Users/will9709/code/seismic-solidity/build/standard-solc/solc-0.8.29");

    let compiler_version = semver::Version::new(0, 8, 29);
    
    let source_content = r#"// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract ShieldedWallet {
    saddress private owner;
    suint256 private balance;
    
    event FundsAdded(uint256 timestamp);
    event FundsWithdrawn(uint256 timestamp);
    
    constructor(saddress _owner, suint256 _initialBalance) {
        owner = _owner;
        balance = _initialBalance;
    }
    
    modifier onlyOwner() {
        require(saddress(msg.sender) == owner, "Not authorized");
        _;
    }
    
    function addFunds(suint256 amount) external onlyOwner {
        balance += amount;
        emit FundsAdded(block.timestamp);
    }
    
    function withdraw(suint256 amount) external onlyOwner {
        require(balance >= amount, "Insufficient balance");
        balance -= amount;
        emit FundsWithdrawn(block.timestamp);
    }
    
    function getBalance() external view onlyOwner returns (uint256) {
        return uint256(balance);
    }
    
    function isOwner(saddress addr) external view returns (bool) {
        return addr == owner;
    }
}"#;

    let source = artifacts::Source::new(source_content);
    let mut sources = BTreeMap::new();
    sources.insert(std::path::PathBuf::from("Test.sol"), source);

    let input = artifacts::SolcInput {
        language: SolcLanguage::Solidity,
        sources: artifacts::Sources(sources),
        settings: artifacts::Settings {
            evm_version: None,
            ..Default::default()
        },
    };

    let solc = foundry_compilers_new::solc::Solc::new_with_version(compiler_path, compiler_version);
    
    println!("Starting compilation...");
    match solc.async_compile_output(&input).await {
        Ok(output) => {
            println!("Compiler returned output (size: {} bytes)", output.len());
            
            // 检查输出中是否有错误
            match serde_json::from_slice::<serde_json::Value>(&output) {
                Ok(json) => {
                    if let Some(errors) = json.get("errors") {
                        if let Some(error_array) = errors.as_array() {
                            let has_errors = error_array.iter().any(|err| {
                                err.get("severity").and_then(|s| s.as_str()) == Some("error")
                            });
                            
                            if has_errors {
                                println!("❌ Compilation has errors:");
                                println!("{}", serde_json::to_string_pretty(errors)?);
                                return Err("Compilation failed with errors".into());
                            } else {
                                println!("⚠️  Compilation has warnings only");
                            }
                        }
                    }
                    
                    if let Some(contracts) = json.get("contracts") {
                        println!("✅ Compilation successful, contracts generated");
                    } else {
                        println!("⚠️  No contracts in output");
                    }
                }
                Err(e) => {
                    println!("❌ Failed to parse JSON output: {}", e);
                    println!("Raw output: {}", String::from_utf8_lossy(&output));
                    return Err(e.into());
                }
            }
        }
        Err(e) => {
            println!("❌ Compilation process failed: {:?}", e);
            return Err(e.into());
        }
    }

    Ok(())
}