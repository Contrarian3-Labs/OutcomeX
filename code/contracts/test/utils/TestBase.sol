// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface Vm {
    function prank(address caller) external;
    function startPrank(address caller) external;
    function stopPrank() external;
    function expectRevert(bytes calldata) external;
    function expectEmit(bool, bool, bool, bool, address) external;
    function warp(uint256 newTimestamp) external;
}

contract TestBase {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function assertEq(uint256 left, uint256 right, string memory err) internal pure {
        require(left == right, err);
    }

    function assertEq(address left, address right, string memory err) internal pure {
        require(left == right, err);
    }

    function assertEq(bytes32 left, bytes32 right, string memory err) internal pure {
        require(left == right, err);
    }

    function assertTrue(bool condition, string memory err) internal pure {
        require(condition, err);
    }

    function assertGt(uint256 left, uint256 right, string memory err) internal pure {
        require(left > right, err);
    }
}
