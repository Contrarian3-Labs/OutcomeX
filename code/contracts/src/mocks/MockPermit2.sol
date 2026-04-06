// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {SimpleERC20} from "../common/SimpleERC20.sol";
import {IPermit2} from "../interfaces/IPermit2.sol";

contract MockPermit2 is IPermit2 {
    function permitTransferFrom(
        PermitTransferFrom calldata permit,
        SignatureTransferDetails calldata transferDetails,
        address owner,
        bytes calldata signature
    ) external {
        require(signature.length > 0, "EMPTY_SIGNATURE");
        require(block.timestamp <= permit.deadline, "PERMIT_EXPIRED");
        require(transferDetails.requestedAmount <= permit.permitted.amount, "AMOUNT_EXCEEDS_PERMIT");
        bool success =
            SimpleERC20(permit.permitted.token).transferFrom(owner, transferDetails.to, transferDetails.requestedAmount);
        require(success, "TRANSFER_FAILED");
    }
}
