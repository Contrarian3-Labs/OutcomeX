// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {PWRToken} from "./PWRToken.sol";
import {IRevenueVault} from "./interfaces/IRevenueVault.sol";

interface IMachineAssetOwnership {
    function ownerOf(uint256 machineId) external view returns (address);
}

error NotSettlementController(address caller);

contract RevenueVault is Ownable, IRevenueVault {
    PWRToken public immutable pwrToken;
    IMachineAssetOwnership public immutable machineAsset;

    address public settlementController;

    mapping(uint256 => uint256) public unsettledRevenueByMachine;
    mapping(uint256 => mapping(address => uint256)) public claimableByMachineOwner;
    mapping(uint256 => uint256) public nonDividendRevenueByMachine;

    event SettlementControllerSet(address indexed previousController, address indexed newController);
    event RevenueAccrued(
        uint256 indexed machineId,
        uint256 indexed orderId,
        address indexed machineOwner,
        uint256 amount,
        bool dividendEligible
    );
    event MachineRevenueClaimedDetailed(
        uint256 indexed machineId,
        address indexed machineOwner,
        uint256 amount,
        uint256 remainingClaimableForMachineOwnerAfter,
        uint256 remainingUnsettledRevenueByMachineAfter
    );

    constructor(address initialOwner, address pwrTokenAddress, address machineAssetAddress) Ownable(initialOwner) {
        pwrToken = PWRToken(pwrTokenAddress);
        machineAsset = IMachineAssetOwnership(machineAssetAddress);
    }

    modifier onlySettlementController() {
        if (msg.sender != settlementController) {
            revert NotSettlementController(msg.sender);
        }
        _;
    }

    function setSettlementController(address controller) external onlyOwner {
        address previousController = settlementController;
        settlementController = controller;
        emit SettlementControllerSet(previousController, controller);
    }

    function accrueRevenue(
        uint256 machineId,
        address machineOwner,
        uint256 orderId,
        uint256 amount,
        bool dividendEligible
    ) external onlySettlementController {
        if (amount == 0) {
            return;
        }

        if (dividendEligible) {
            unsettledRevenueByMachine[machineId] += amount;
            claimableByMachineOwner[machineId][machineOwner] += amount;
            pwrToken.mint(address(this), amount);
        } else {
            nonDividendRevenueByMachine[machineId] += amount;
        }

        emit RevenueAccrued(machineId, orderId, machineOwner, amount, dividendEligible);
    }

    function claim(uint256 machineId) external returns (uint256 amount) {
        amount = claimableByMachineOwner[machineId][msg.sender];
        require(amount > 0, "NOTHING_TO_CLAIM");

        claimableByMachineOwner[machineId][msg.sender] = 0;
        unsettledRevenueByMachine[machineId] -= amount;

        bool success = pwrToken.transfer(msg.sender, amount);
        require(success, "PWR_TRANSFER_FAILED");

        emit MachineRevenueClaimedDetailed(
            machineId,
            msg.sender,
            amount,
            claimableByMachineOwner[machineId][msg.sender],
            unsettledRevenueByMachine[machineId]
        );
    }

    function hasUnsettledRevenue(uint256 machineId) external view returns (bool) {
        return unsettledRevenueByMachine[machineId] > 0;
    }
}
