# base_bg: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python-based simulation of a validator node's event listener component for a cross-chain bridge. It is designed as a robust, well-architected example of how a real-world decentralized application's backend service might be structured. The script listens for `TokensLocked` events on a source blockchain and simulates the process of creating, signing, and submitting a corresponding `claimTokens` transaction on a destination blockchain.

## Concept

A cross-chain bridge allows users to transfer assets or data from one blockchain to another. A common architecture involves a 'lock-and-mint' or 'burn-and-release' mechanism. This script simulates the crucial off-chain component responsible for this process:

1.  A user deposits and locks tokens into a bridge contract on the **Source Chain** (e.g., Ethereum).
2.  This action emits a `TokensLocked` event, containing details like the recipient's address, the token, the amount, and a unique nonce.
3.  An off-chain service, the **Event Listener** (this script), continuously monitors the Source Chain for these events.
4.  Upon detecting a new `TokensLocked` event, the listener validates it and constructs a new transaction to call the `claimTokens` function on the bridge contract on the **Destination Chain** (e.g., Polygon).
5.  This transaction, signed by a trusted validator, authorizes the minting or releasing of equivalent tokens to the user on the Destination Chain.

This script simulates steps 3, 4, and 5, providing a blueprint for a reliable and scalable listener.

## Code Architecture

The script is designed with a clear separation of concerns, using multiple classes to handle distinct responsibilities. This makes the system easier to understand, maintain, and extend.

```
+----------------------------+
|           main()           |
| (Entry Point)              |
+-------------+--------------+
              |
              v
+-------------+--------------+
| CrossChainBridgeListener   |
| (Orchestrator)             |
+----------------------------+
| - Manages the main loop    |
| - Handles block persistence|
| - Coordinates all components|
+----+------------------+----+------------------+
     |                  |                       |
     v                  v                       v
+----+-------------+ +--+-------------------+ +-+----------------------+
| BlockchainConnector| |   EventProcessor      | | TransactionSubmitter   |
| (Web3 Wrapper)     | | (Business Logic)      | | (Transaction Sender)     |
+--------------------+ +-----------------------+ +------------------------+
| - Connects to RPC  | | - Parses event data   | | - Builds transactions    |
| - Gets contracts   | | - Validates events    | | - Manages account nonce  |
|                    | | - Prepares claim data | | - Signs & sends transactions |
+--------------------+ +-----------------------+ +------------------------+
```

-   **`BridgeConfig`**: A dedicated class for loading and validating all necessary configuration from environment variables (`.env` file). This centralizes configuration management.
-   **`BlockchainConnector`**: A wrapper around the `web3.py` library. It abstracts the complexities of connecting to an EVM RPC endpoint, loading contract ABIs, and interacting with smart contracts. The listener uses two instances of this class: one for the source chain and one for the destination chain.
-   **`EventProcessor`**: Contains the core business logic. It takes raw event data, validates it against a set of rules (e.g., is it for the correct destination chain? has it been processed before?), and transforms it into a clean data structure ready for the next step.
-   **`TransactionSubmitter`**: Manages all aspects of creating and sending a transaction to the destination chain. It handles gas price estimation, account nonce management, transaction signing with the validator's private key, and (simulated) submission.
-   **`CrossChainBridgeListener`**: The main orchestrator. It initializes all other components and runs the primary infinite loop. It is responsible for polling for new blocks, fetching events, passing them to the `EventProcessor`, and then passing the results to the `TransactionSubmitter`. It also manages persistence by saving the last scanned block number to a file.

## How it Works

The listener operates in a continuous loop:

1.  **Initialization**: The script loads configuration from a `.env` file, establishes connections to the RPC endpoints for both the source and destination chains, and instantiates the necessary smart contract objects.
2.  **State Restoration**: It reads a local file (`last_processed_block.dat`) to determine which block to start scanning from. This ensures that if the script restarts, it doesn't re-process old events or miss any new ones. If the file doesn't exist, it starts from a recent block.
3.  **Polling**: The main loop begins. In each iteration, it checks the latest block number on the source chain.
4.  **Scanning**: It defines a block range to scan (e.g., from the last processed block up to the latest block, capped at a certain step size to avoid overwhelming the RPC node).
5.  **Event Filtering**: It uses `web3.py`'s event filtering capabilities to query the source chain for any `TokensLocked` events within that block range.
6.  **Processing**: If events are found, they are passed one-by-one to the `EventProcessor` for validation.
7.  **Submission**: If an event is valid, the `EventProcessor` returns structured data, which is then passed to the `TransactionSubmitter`.
8.  **Signing & Sending**: The `TransactionSubmitter` builds the `claimTokens` transaction, signs it with the validator's private key, and **simulates** sending it to the destination chain. In this simulation, it logs the transaction hash that *would have been* sent.
9.  **State Update**: After scanning a range, the script updates the `last_processed_block.dat` file with the latest block number it has processed.
10. **Wait**: The script waits for a configurable interval (e.g., 15 seconds) before starting the loop again from step 3.

Error handling is included for RPC connection issues and other potential failures to ensure the listener is resilient.

## Usage

### 1. Prerequisites

-   Python 3.8+
-   An `.env` file with your configuration.

### 2. Setup

Clone the repository:
```bash
git clone <repository_url>
cd base_bg
```

Create a Python virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
```

Install the required dependencies:
```bash
pip install -r requirements.txt
```

Create a `.env` file in the root of the project and populate it with the necessary details. You can use free RPC URLs from services like Infura, Alchemy, or public nodes. **Ensure `.env` is added to your `.gitignore` file to prevent accidentally committing secrets.**

**Example `.env` file:**
```dotenv
# --- Source Chain (e.g., Ethereum Goerli Testnet) ---
SOURCE_RPC_URL="https://goerli.infura.io/v3/YOUR_INFURA_PROJECT_ID"
SOURCE_BRIDGE_CONTRACT_ADDRESS="0xSourceBridgeContractAddress..."

# --- Destination Chain (e.g., Polygon Mumbai Testnet) ---
DEST_RPC_URL="https://polygon-mumbai.infura.io/v3/YOUR_INFURA_PROJECT_ID"
DEST_BRIDGE_CONTRACT_ADDRESS="0xDestinationBridgeContractAddress..."

# --- Validator Wallet ---
# IMPORTANT: Use a dedicated, firewalled account. NEVER commit this file with your key.
VALIDATOR_PRIVATE_KEY="your_validator_private_key_without_the_0x_prefix..."
```

### 3. Running the Script

The main entry point of the application initializes the `CrossChainBridgeListener` and starts its main `run` loop within a `try...except` block to catch any critical failures on startup.

```python
# script.py (simplified example of the main entry point)

from listener import CrossChainBridgeListener
import logging

# Basic logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(module)s] - %(message)s')

if __name__ == "__main__":
    try:
        listener = CrossChainBridgeListener()
        listener.run()
    except Exception as e:
        logging.critical(f"A critical error occurred on startup: {e}", exc_info=True)
```

Execute the main script from your terminal:
```bash
python script.py
```

The listener will start, and you will see logs indicating its activity, such as connecting to the blockchains, scanning block ranges, and processing events.

**Example Output:**
```
2023-10-27 15:30:00 - INFO - [bridge_config] - Loading configuration from environment variables...
2023-10-27 15:30:00 - INFO - [bridge_config] - Configuration loaded successfully. Validator Address: 0x...
2023-10-27 15:30:00 - INFO - [blockchain_connector] - Connecting to SourceChain chain at https://goerli.infura.io/v3/...
2023-10-27 15:30:02 - INFO - [blockchain_connector] - Successfully connected to SourceChain. Chain ID: 5
2023-10-27 15:30:02 - INFO - [blockchain_connector] - Connecting to DestinationChain chain at https://polygon-mumbai.infura.io/v3/...
2023-10-27 15:30:04 - INFO - [blockchain_connector] - Successfully connected to DestinationChain. Chain ID: 80001
2023-10-27 15:30:04 - INFO - [script] - Starting cross-chain event listener...
2023-10-27 15:30:05 - INFO - [script] - Scanning for 'TokensLocked' events from block 9814501 to 9819501.
2023-10-27 15:30:08 - INFO - [script] - Found 1 new event(s) in the specified block range.
2023-10-27 15:30:08 - INFO - [event_processor] - Processing event from Tx 0x... (Log Index: 45) with nonce 123.
2023-10-27 15:30:08 - INFO - [event_processor] - Event validation successful for nonce 123. Preparing claim transaction.
2023-10-27 15:30:09 - INFO - [transaction_submitter] - Building claim transaction for nonce 123 with account nonce 42.
2023-10-27 15:30:10 - INFO - [transaction_submitter] - [SIMULATED] Transaction sent. Tx Hash: 0x...
2023-10-27 15:30:12 - INFO - [script] - Scanning for 'TokensLocked' events from block 9819502 to 9824502.
2023-10-27 15:30:15 - INFO - [script] - No new events found in this range.
...
```