import os
import time
import json
import logging
from typing import Dict, Any, Optional

import requests
from web3 import Web3
from web3.contract import Contract
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv
from hexbytes import HexBytes

# --- Basic Configuration ---
load_dotenv() # Load environment variables from .env file

# Configure logging to provide detailed output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(module)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Constants and Mock Data ---

# To simulate, we define a simplified ABI for the bridge contracts.
# In a real-world scenario, this would be loaded from a JSON file.
BRIDGE_CONTRACT_ABI = json.loads('''
[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "internalType": "address", "name": "from", "type": "address"},
            {"indexed": true, "internalType": "address", "name": "token", "type": "address"},
            {"indexed": false, "internalType": "uint256", "name": "amount", "type": "uint256"},
            {"indexed": false, "internalType": "uint256", "name": "nonce", "type": "uint256"},
            {"indexed": false, "internalType": "uint256", "name": "destinationChainId", "type": "uint256"}
        ],
        "name": "TokensLocked",
        "type": "event"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "uint256", "name": "sourceNonce", "type": "uint256"}
        ],
        "name": "claimTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]
''')

# File to persist the last processed block number
PERSISTENCE_FILE = 'last_processed_block.dat'

class BridgeConfig:
    """A dedicated class to handle configuration loading and validation."""
    def __init__(self) -> None:
        logging.info("Loading configuration from environment variables...")
        self.source_rpc_url: str = os.getenv('SOURCE_RPC_URL')
        self.dest_rpc_url: str = os.getenv('DEST_RPC_URL')
        self.source_bridge_contract_address: str = os.getenv('SOURCE_BRIDGE_CONTRACT_ADDRESS')
        self.dest_bridge_contract_address: str = os.getenv('DEST_BRIDGE_CONTRACT_ADDRESS')
        self.validator_private_key: str = os.getenv('VALIDATOR_PRIVATE_KEY')

        if not all([self.source_rpc_url, self.dest_rpc_url, self.source_bridge_contract_address, self.dest_bridge_contract_address, self.validator_private_key]):
            raise ValueError("One or more required environment variables are missing. Please check your .env file.")
        
        # Ensure addresses are checksummed
        self.source_bridge_contract_address = Web3.to_checksum_address(self.source_bridge_contract_address)
        self.dest_bridge_contract_address = Web3.to_checksum_address(self.dest_bridge_contract_address)

        # Extract validator address from private key
        self.validator_address = Web3.to_checksum_address(Web3.get_key_info(self.validator_private_key)[1])
        logging.info(f"Configuration loaded successfully. Validator Address: {self.validator_address}")

class BlockchainConnector:
    """Manages connection to an EVM-compatible blockchain via Web3.py."""
    def __init__(self, rpc_url: str, chain_name: str):
        """
        Initializes the connector.
        :param rpc_url: The JSON-RPC endpoint of the blockchain node.
        :param chain_name: A human-readable name for the chain (for logging).
        """
        self.rpc_url = rpc_url
        self.chain_name = chain_name
        self.w3: Optional[Web3] = None
        self.connect()

    def connect(self) -> None:
        """Establishes the connection to the blockchain node."""
        try:
            logging.info(f"Connecting to {self.chain_name} chain at {self.rpc_url}...")
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            # Middleware for PoA chains like Goerli, Sepolia, etc.
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            
            if not self.w3.is_connected():
                raise ConnectionError("Failed to connect to the node.")
            
            logging.info(f"Successfully connected to {self.chain_name}. Chain ID: {self.w3.eth.chain_id}")
        except Exception as e:
            logging.error(f"Error connecting to {self.chain_name}: {e}")
            self.w3 = None # Ensure w3 is None on failure
            raise

    def get_contract_instance(self, contract_address: str, abi: list) -> Optional[Contract]:
        """
        Creates a Web3.py contract instance.
        :param contract_address: The address of the smart contract.
        :param abi: The ABI of the smart contract.
        :return: A Web3.py Contract object or None if connection failed.
        """
        if not self.w3 or not self.w3.is_connected():
            logging.error(f"Cannot get contract instance. Not connected to {self.chain_name}.")
            return None
        return self.w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)

class EventProcessor:
    """
    Handles the business logic of processing a detected event.
    This class is responsible for parsing, validating, and preparing the claim transaction data.
    """
    def __init__(self, dest_chain_id: int):
        self.dest_chain_id = dest_chain_id
        self.processed_nonces = set() # In-memory cache to prevent duplicate processing within a session

    def process_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Processes a single 'TokensLocked' event.
        :param event: The event data dictionary from Web3.py.
        :return: A dictionary with parameters for the claim transaction, or None if invalid.
        """
        try:
            log_index = event['logIndex']
            tx_hash = event['transactionHash'].hex()
            event_args = event['args']
            nonce = event_args['nonce']

            logging.info(f"Processing event from Tx {tx_hash} (Log Index: {log_index}) with nonce {nonce}.")

            # --- Core Validation Logic ---
            # 1. Check if the event is intended for our destination chain
            if event_args['destinationChainId'] != self.dest_chain_id:
                logging.warning(f"Skipping event with nonce {nonce}: intended for chain {event_args['destinationChainId']}, not {self.dest_chain_id}.")
                return None

            # 2. Check for duplicate processing using the unique nonce from the source chain
            if nonce in self.processed_nonces:
                logging.warning(f"Skipping event with nonce {nonce}: already processed in this session.")
                return None

            # 3. Add more validations here (e.g., check against a list of supported tokens, amount limits, etc.)
            if event_args['amount'] <= 0:
                logging.error(f"Invalid event with nonce {nonce}: amount is zero or negative.")
                return None
            
            logging.info(f"Event validation successful for nonce {nonce}. Preparing claim transaction.")

            # Prepare data for the `claimTokens` function call on the destination chain
            claim_data = {
                'to': event_args['from'], # Claim goes to the original sender
                'token': event_args['token'], # This might need to be a mapped address on the destination chain
                'amount': event_args['amount'],
                'sourceNonce': nonce
            }
            
            self.processed_nonces.add(nonce)
            return claim_data
        except KeyError as e:
            logging.error(f"Malformed event data. Missing key: {e}. Event: {event}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during event processing: {e}")
            return None

class TransactionSubmitter:
    """
    Responsible for signing and submitting transactions to the destination chain.
    Handles nonce management and gas estimation for the validator account.
    """
    def __init__(self, connector: BlockchainConnector, config: BridgeConfig):
        self.connector = connector
        self.config = config
        self.dest_contract = self.connector.get_contract_instance(config.dest_bridge_contract_address, BRIDGE_CONTRACT_ABI)
        if not self.dest_contract:
            raise ConnectionError("Failed to instantiate destination contract.")

    def _get_gas_price(self) -> int:
        """Fetches the current gas price, with a fallback."""
        try:
            # For more sophisticated strategies, an API like ETH Gas Station could be used via `requests`
            return self.connector.w3.eth.gas_price
        except Exception as e:
            logging.warning(f"Could not fetch gas price, using fallback. Error: {e}")
            return self.connector.w3.to_wei('20', 'gwei') # Fallback value

    def submit_claim_transaction(self, claim_data: Dict[str, Any]) -> Optional[HexBytes]:
        """
        Builds, signs, and sends the 'claimTokens' transaction.
        :param claim_data: The processed data from EventProcessor.
        :return: The transaction hash if successful, otherwise None.
        """
        if not self.connector.w3 or not self.connector.w3.is_connected():
            logging.error("Cannot submit transaction. Not connected to destination chain.")
            return None

        try:
            validator_address = self.config.validator_address
            nonce = self.connector.w3.eth.get_transaction_count(validator_address)
            logging.info(f"Building claim transaction for nonce {claim_data['sourceNonce']} with account nonce {nonce}.")

            # Build the transaction object
            tx = self.dest_contract.functions.claimTokens(
                claim_data['to'],
                claim_data['token'],
                claim_data['amount'],
                claim_data['sourceNonce']
            ).build_transaction({
                'chainId': self.connector.w3.eth.chain_id,
                'from': validator_address,
                'nonce': nonce,
                'gasPrice': self._get_gas_price(),
                # 'gas': 200000 # Let web3 estimate, or set a fixed value
            })

            # Sign the transaction
            signed_tx = self.connector.w3.eth.account.sign_transaction(tx, private_key=self.config.validator_private_key)

            # --- SIMULATION VS REAL SEND ---
            # In a real system, you would uncomment the following line:
            # tx_hash = self.connector.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            # For this simulation, we will just log the hash of the signed transaction.
            tx_hash = signed_tx.hash
            
            logging.info(f"[SIMULATED] Transaction sent. Tx Hash: {tx_hash.hex()}")
            
            # Example of waiting for receipt (in a real scenario)
            # receipt = self.connector.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            # if receipt.status == 1:
            #     logging.info("Claim transaction successful!")
            # else:
            #     logging.error("Claim transaction failed!")
            
            return tx_hash

        except Exception as e:
            logging.error(f"Failed to submit claim transaction for source nonce {claim_data['sourceNonce']}. Error: {e}")
            # This could be due to gas issues, reverted transaction, etc.
            return None

class CrossChainBridgeListener:
    """Main orchestrator class for the bridge listener service."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.source_connector = BlockchainConnector(config.source_rpc_url, "SourceChain")
        self.dest_connector = BlockchainConnector(config.dest_rpc_url, "DestinationChain")

        if not self.source_connector.w3 or not self.dest_connector.w3:
            raise ConnectionError("Failed to initialize blockchain connectors. Aborting.")

        self.source_contract = self.source_connector.get_contract_instance(config.source_bridge_contract_address, BRIDGE_CONTRACT_ABI)
        self.event_processor = EventProcessor(dest_chain_id=self.dest_connector.w3.eth.chain_id)
        self.tx_submitter = TransactionSubmitter(self.dest_connector, config)
        
        self.poll_interval_seconds = 15 # Time to wait between polling for new blocks
        self.block_step = 5000 # Number of blocks to scan at a time

    def _get_last_processed_block(self) -> int:
        """Retrieves the last processed block number from a persistence file."""
        try:
            if os.path.exists(PERSISTENCE_FILE):
                with open(PERSISTENCE_FILE, 'r') as f:
                    return int(f.read().strip())
            else:
                # If file doesn't exist, start from the current block minus a small buffer
                start_block = self.source_connector.w3.eth.block_number - 10
                logging.warning(f"Persistence file not found. Starting scan from block {start_block}.")
                return start_block
        except Exception as e:
            logging.error(f"Error reading persistence file. Defaulting to current block. Error: {e}")
            return self.source_connector.w3.eth.block_number - 1

    def _save_last_processed_block(self, block_number: int) -> None:
        """Saves the last processed block number to the persistence file."""
        try:
            with open(PERSISTENCE_FILE, 'w') as f:
                f.write(str(block_number))
        except Exception as e:
            logging.error(f"Could not save last processed block {block_number}: {e}")

    def listen_for_events(self) -> None:
        """The main event loop that continuously polls the source chain for new events."""
        logging.info("Starting cross-chain event listener...")
        last_processed_block = self._get_last_processed_block()

        while True:
            try:
                latest_block = self.source_connector.w3.eth.block_number
                
                # Determine the range of blocks to scan
                from_block = last_processed_block + 1
                to_block = min(latest_block, from_block + self.block_step)

                if from_block > latest_block:
                    logging.info(f"No new blocks to process. Current head is {latest_block}. Sleeping for {self.poll_interval_seconds}s.")
                    time.sleep(self.poll_interval_seconds)
                    continue

                logging.info(f"Scanning for 'TokensLocked' events from block {from_block} to {to_block}.")

                # Create a filter for the 'TokensLocked' event
                event_filter = self.source_contract.events.TokensLocked.create_filter(
                    fromBlock=from_block,
                    toBlock=to_block
                )
                events = event_filter.get_all_entries()

                if events:
                    logging.info(f"Found {len(events)} new event(s) in the specified block range.")
                    for event in sorted(events, key=lambda e: e['blockNumber']): # Process in order
                        claim_data = self.event_processor.process_event(event)
                        if claim_data:
                            self.tx_submitter.submit_claim_transaction(claim_data)
                else:
                    logging.info("No new events found in this range.")

                # Update the last processed block and persist it
                last_processed_block = to_block
                self._save_last_processed_block(last_processed_block)
                time.sleep(2) # Small delay to prevent API rate limiting

            except requests.exceptions.ConnectionError as e:
                logging.error(f"RPC connection error: {e}. Retrying in {self.poll_interval_seconds} seconds...")
                time.sleep(self.poll_interval_seconds)
                # Attempt to reconnect
                self.source_connector.connect()
            except Exception as e:
                logging.critical(f"An unhandled exception occurred in the main loop: {e}", exc_info=True)
                time.sleep(self.poll_interval_seconds * 2) # Longer sleep on critical error

def main():
    """Entry point for the script."""
    try:
        config = BridgeConfig()
        listener = CrossChainBridgeListener(config)
        listener.listen_for_events()
    except (ValueError, ConnectionError) as e:
        logging.critical(f"Initialization failed: {e}")
    except KeyboardInterrupt:
        logging.info("Shutdown signal received. Exiting listener.")
    except Exception as e:
        logging.critical(f"A fatal error occurred during startup: {e}", exc_info=True)

if __name__ == '__main__':
    main()
