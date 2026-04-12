// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {IRevenueVault} from "./interfaces/IRevenueVault.sol";
import {SettlementBreakdown, SettlementInput, SettlementKind} from "./types/OutcomeXTypes.sol";

error NotOrderBook(address caller);

interface IERC20Like {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract SettlementController is Ownable {
    uint256 public constant BPS_DENOMINATOR = 10_000;
    uint256 public constant PLATFORM_FEE_BPS = 1_000;
    uint256 public constant VALID_PREVIEW_REJECT_REFUND_BPS = 7_000;

    IRevenueVault public immutable revenueVault;
    address public immutable pwrToken;

    address public orderBook;
    address public platformTreasury;

    mapping(address => uint256) public refundableUSDT;
    uint256 public platformAccruedUSDT;
    mapping(address => mapping(address => uint256)) public refundableByToken;
    mapping(address => uint256) public platformAccruedByToken;

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
    event RefundClaimedDetailed(
        address indexed buyer, address indexed token, uint256 amount, uint256 remainingRefundableAfter
    );
    event PlatformRevenueClaimedDetailed(
        address indexed treasury, address indexed token, uint256 amount, uint256 remainingPlatformAccruedAfter
    );

    constructor(address initialOwner, address revenueVaultAddress, address pwrTokenAddress, address initialTreasury) Ownable(initialOwner) {
        require(pwrTokenAddress != address(0), "ZERO_PWR_TOKEN");
        revenueVault = IRevenueVault(revenueVaultAddress);
        pwrToken = pwrTokenAddress;
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
            if (input.paymentToken == address(0)) {
                refundableUSDT[input.buyer] += breakdown.refundToBuyer;
            } else {
                refundableByToken[input.buyer][input.paymentToken] += breakdown.refundToBuyer;
            }
        }

        if (breakdown.platformShare > 0) {
            if (input.paymentToken == address(0)) {
                platformAccruedUSDT += breakdown.platformShare;
            } else {
                platformAccruedByToken[input.paymentToken] += breakdown.platformShare;
            }
        }

        if (breakdown.machineShare > 0) {
            if (input.paymentToken == pwrToken && breakdown.dividendEligible) {
                bool funded = IERC20Like(pwrToken).transfer(address(revenueVault), breakdown.machineShare);
                require(funded, "PWR_REVENUE_FUNDING_FAILED");
            }
            revenueVault.accrueRevenue(
                input.machineId,
                input.settlementBeneficiary,
                input.orderId,
                breakdown.machineShare,
                breakdown.dividendEligible,
                input.paymentToken == pwrToken
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
        emit RefundClaimedDetailed(msg.sender, address(0), amount, refundableUSDT[msg.sender]);
    }

    function claimRefund(address token) external returns (uint256 amount) {
        require(token != address(0), "ZERO_TOKEN");

        amount = refundableByToken[msg.sender][token];
        require(amount > 0, "NOTHING_TO_CLAIM");

        refundableByToken[msg.sender][token] = 0;
        bool success = IERC20Like(token).transfer(msg.sender, amount);
        require(success, "TOKEN_TRANSFER_FAILED");

        emit RefundClaimedDetailed(msg.sender, token, amount, refundableByToken[msg.sender][token]);
    }

    function claimPlatformRevenue() external returns (uint256 amount) {
        require(msg.sender == platformTreasury, "NOT_TREASURY");

        amount = platformAccruedUSDT;
        require(amount > 0, "NOTHING_TO_CLAIM");

        platformAccruedUSDT = 0;
        emit PlatformRevenueClaimedDetailed(msg.sender, address(0), amount, platformAccruedUSDT);
    }

    function claimPlatformRevenue(address token) external returns (uint256 amount) {
        require(msg.sender == platformTreasury, "NOT_TREASURY");
        require(token != address(0), "ZERO_TOKEN");

        amount = platformAccruedByToken[token];
        require(amount > 0, "NOTHING_TO_CLAIM");

        platformAccruedByToken[token] = 0;
        bool success = IERC20Like(token).transfer(msg.sender, amount);
        require(success, "TOKEN_TRANSFER_FAILED");

        emit PlatformRevenueClaimedDetailed(msg.sender, token, amount, platformAccruedByToken[token]);
    }
}
