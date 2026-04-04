// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./lib/Ownable.sol";
import {SimpleERC20} from "./lib/SimpleERC20.sol";

error NotMinter(address caller);

contract PWRToken is Ownable, SimpleERC20 {
    mapping(address => bool) public isMinter;

    event MinterSet(address indexed minter, bool isEnabled);

    constructor(address initialOwner) Ownable(initialOwner) SimpleERC20("OutcomeX Power", "PWR", 18) {}

    modifier onlyMinter() {
        if (!isMinter[msg.sender]) {
            revert NotMinter(msg.sender);
        }
        _;
    }

    function setMinter(address minter, bool enabled) external onlyOwner {
        isMinter[minter] = enabled;
        emit MinterSet(minter, enabled);
    }

    function mint(address to, uint256 amount) external onlyMinter {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyMinter {
        _burn(from, amount);
    }
}
