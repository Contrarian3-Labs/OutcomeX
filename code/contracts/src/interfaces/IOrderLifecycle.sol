// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {OrderRecord} from "../types/OutcomeXTypes.sol";

interface IOrderLifecycle {
    function createOrder(uint256 machineId, uint256 grossAmount) external returns (uint256 orderId);
    function markOrderPaid(
        uint256 orderId, bool dividendEligible, bool refundFailedOrNoValidPreviewAuthorized, address paymentToken
    ) external;
    function settlementBeneficiaryByOrder(uint256 orderId) external view returns (address);
    function markPreviewReady(uint256 orderId, bool validPreview) external;
    function confirmResult(uint256 orderId) external;
    function rejectValidPreview(uint256 orderId) external;
    function refundFailedOrNoValidPreview(uint256 orderId) external;
    function getOrder(uint256 orderId) external view returns (OrderRecord memory);
}
