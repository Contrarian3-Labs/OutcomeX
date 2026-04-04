// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IRevenueVault {
    function accrueRevenue(
        uint256 machineId,
        address machineOwner,
        uint256 orderId,
        uint256 amount,
        bool dividendEligible
    ) external;

    function hasUnsettledRevenue(uint256 machineId) external view returns (bool);
}
