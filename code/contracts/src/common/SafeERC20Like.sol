// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

library SafeERC20Like {
    bytes4 private constant TRANSFER_SELECTOR = 0xa9059cbb;
    bytes4 private constant TRANSFER_FROM_SELECTOR = 0x23b872dd;

    function safeTransfer(address token, address to, uint256 amount) internal returns (bool) {
        (bool success, bytes memory returndata) =
            token.call(abi.encodeWithSelector(TRANSFER_SELECTOR, to, amount));
        return success && _didOptionalReturnSucceed(returndata);
    }

    function safeTransferFrom(address token, address from, address to, uint256 amount) internal returns (bool) {
        (bool success, bytes memory returndata) =
            token.call(abi.encodeWithSelector(TRANSFER_FROM_SELECTOR, from, to, amount));
        return success && _didOptionalReturnSucceed(returndata);
    }

    function _didOptionalReturnSucceed(bytes memory returndata) private pure returns (bool) {
        if (returndata.length == 0) {
            return true;
        }
        if (returndata.length == 32) {
            return abi.decode(returndata, (bool));
        }
        return false;
    }
}
