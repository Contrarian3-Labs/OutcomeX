// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SimpleERC20} from "../common/SimpleERC20.sol";

contract MockUSDT is SimpleERC20 {
    constructor() SimpleERC20("Tether", "USDT", 6) {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}
