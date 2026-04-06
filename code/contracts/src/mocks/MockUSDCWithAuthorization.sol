// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SimpleERC20} from "../common/SimpleERC20.sol";

contract MockUSDCWithAuthorization is SimpleERC20 {
    mapping(address => mapping(bytes32 => bool)) public authorizationUsed;

    constructor() SimpleERC20("USD Coin", "USDC", 6) {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8,
        bytes32,
        bytes32
    ) external {
        require(block.timestamp >= validAfter, "AUTH_NOT_YET_VALID");
        require(block.timestamp <= validBefore, "AUTH_EXPIRED");
        require(!authorizationUsed[from][nonce], "AUTH_ALREADY_USED");
        authorizationUsed[from][nonce] = true;
        _transfer(from, to, value);
    }
}
