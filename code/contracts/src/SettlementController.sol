// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {IRevenueVault} from "./interfaces/IRevenueVault.sol";
import {SettlementBreakdown, SettlementInput, SettlementKind} from "./types/OutcomeXTypes.sol";

error NotOrderBook(address caller);

contract SettlementController is Ownable {
    uint256 public constant BPS_DENOMINATOR = 10_000;
    uint256 public constant PLATFORM_FEE_BPS = 1_000;
    uint256 public constant VALID_PREVIEW_REJECT_REFUND_BPS = 7_000;

    IRevenueVault public immutable revenueVault;

    address public orderBook;
    address public platformTreasury;

    mapping(address => uint256) public refundableUSDT;
    uint256 public platformAccruedUSDT;

    event OrderBookSet(address indexed previousOrderBook, address indexed newOrderBook);
    event PlatformTreasurySet(address indexed previousTreasury, address indexed newTreasury);
    event Settled(
        uint256 indexed orderId,
        uint256 indexed machineId,
        SettlementKind kind,
        address buyer,
        address settlementBeneficiary,
        uint256 grossAmount,
        uint256 refundToBuyer,
        uint256 platformShare,
        uint256 machineShare,
        bool dividendEligible
    );
    event RefundClaimed(address indexed buyer, uint256 amount);
    event PlatformRevenueClaimed(address indexed treasury, uint256 amount);

    constructor(address initialOwner, address revenueVaultAddress, address initialTreasury) Ownable(initialOwner) {
        revenueVault = IRevenueVault(revenueVaultAddress);
        platformTreasury = initialTreasury;
    }

    modifier onlyOrderBook() {
        if (msg.sender != orderBook) {
            revert NotOrderBook(msg.sender);
        }
        _;
    }

    function setOrderBook(address newOrderBook) external onlyOwner {
        address previousOrderBook = orderBook;
        orderBook = newOrderBook;
        emit OrderBookSet(previousOrderBook, newOrderBook);
    }

    function setPlatformTreasury(address newTreasury) external onlyOwner {
        require(newTreasury != address(0), "ZERO_TREASURY");
        address previousTreasury = platformTreasury;
        platformTreasury = newTreasury;
        emit PlatformTreasurySet(previousTreasury, newTreasury);
    }

    function settle(SettlementInput calldata input, SettlementKind kind)
        external
        onlyOrderBook
        returns (SettlementBreakdown memory breakdown)
    {
        breakdown.kind = kind;
        breakdown.dividendEligible = input.dividendEligible;

        if (kind == SettlementKind.Confirmed) {
            breakdown.platformShare = (input.grossAmount * PLATFORM_FEE_BPS) / BPS_DENOMINATOR;
            breakdown.machineShare = input.grossAmount - breakdown.platformShare;
        } else if (kind == SettlementKind.RejectedValidPreview) {
            breakdown.refundToBuyer = (input.grossAmount * VALID_PREVIEW_REJECT_REFUND_BPS) / BPS_DENOMINATOR;
            breakdown.rejectionFee = input.grossAmount - breakdown.refundToBuyer;
            breakdown.platformShare = (breakdown.rejectionFee * PLATFORM_FEE_BPS) / BPS_DENOMINATOR;
            breakdown.machineShare = breakdown.rejectionFee - breakdown.platformShare;
        } else {
            breakdown.refundToBuyer = input.grossAmount;
        }

        if (breakdown.refundToBuyer > 0) {
            refundableUSDT[input.buyer] += breakdown.refundToBuyer;
        }

        if (breakdown.platformShare > 0) {
            platformAccruedUSDT += breakdown.platformShare;
        }

        if (breakdown.machineShare > 0) {
            revenueVault.accrueRevenue(
                input.machineId,
                input.settlementBeneficiary,
                input.orderId,
                breakdown.machineShare,
                breakdown.dividendEligible
            );
        }

        emit Settled(
            input.orderId,
            input.machineId,
            kind,
            input.buyer,
            input.settlementBeneficiary,
            input.grossAmount,
            breakdown.refundToBuyer,
            breakdown.platformShare,
            breakdown.machineShare,
            breakdown.dividendEligible
        );
    }

    function claimRefund() external returns (uint256 amount) {
        amount = refundableUSDT[msg.sender];
        require(amount > 0, "NOTHING_TO_CLAIM");

        refundableUSDT[msg.sender] = 0;
        emit RefundClaimed(msg.sender, amount);
    }

    function claimPlatformRevenue() external returns (uint256 amount) {
        require(msg.sender == platformTreasury, "NOT_TREASURY");

        amount = platformAccruedUSDT;
        require(amount > 0, "NOTHING_TO_CLAIM");

        platformAccruedUSDT = 0;
        emit PlatformRevenueClaimed(msg.sender, amount);
    }
}
