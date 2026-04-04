// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {SimpleERC721} from "./common/SimpleERC721.sol";
import {ITransferGuard} from "./interfaces/ITransferGuard.sol";

error TransferGuardBlocked(uint256 machineId, bytes32 reason);

contract MachineAssetNFT is Ownable, SimpleERC721 {
    uint256 public nextMachineId = 1;
    address public transferGuard;

    mapping(uint256 => string) private _tokenURIs;

    event MachineMinted(uint256 indexed machineId, address indexed owner, string tokenURI);
    event TransferGuardSet(address indexed previousGuard, address indexed newGuard);

    constructor(address initialOwner) Ownable(initialOwner) SimpleERC721("OutcomeX Machine Asset", "OXM") {}

    function mintMachine(address to, string calldata uri) external onlyOwner returns (uint256 machineId) {
        machineId = nextMachineId;
        nextMachineId += 1;

        _mint(to, machineId);
        _tokenURIs[machineId] = uri;

        emit MachineMinted(machineId, to, uri);
    }

    function setTokenURI(uint256 machineId, string calldata uri) external onlyOwner {
        require(_exists(machineId), "UNKNOWN_MACHINE");
        _tokenURIs[machineId] = uri;
    }

    function tokenURI(uint256 machineId) public view override returns (string memory) {
        require(_exists(machineId), "UNKNOWN_MACHINE");
        return _tokenURIs[machineId];
    }

    function setTransferGuard(address newGuard) external onlyOwner {
        address previousGuard = transferGuard;
        transferGuard = newGuard;
        emit TransferGuardSet(previousGuard, newGuard);
    }

    function _beforeTokenTransfer(address from, address to, uint256 machineId) internal view override {
        if (from != address(0) && to != address(0) && transferGuard != address(0)) {
            (bool allowed, bytes32 reason) = ITransferGuard(transferGuard).canTransfer(machineId, from, to);
            if (!allowed) {
                revert TransferGuardBlocked(machineId, reason);
            }
        }
    }
}
