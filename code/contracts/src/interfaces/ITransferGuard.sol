// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ITransferGuard {
    function canTransfer(uint256 machineId, address from, address to) external view returns (bool allowed, bytes32 reason);
}
