// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {MachineAssetNFT} from "./MachineAssetNFT.sol";
import {ITransferGuard} from "./interfaces/ITransferGuard.sol";
import {IOrderLifecycle} from "./interfaces/IOrderLifecycle.sol";
import {IRevenueVault} from "./interfaces/IRevenueVault.sol";
import {SettlementController} from "./SettlementController.sol";
import {OrderRecord, OrderStatus, SettlementBreakdown, SettlementInput, SettlementKind} from "./types/OutcomeXTypes.sol";

error NotPaymentAdapter(address caller);

contract OrderBook is Ownable, ITransferGuard, IOrderLifecycle {
    bytes32 public constant REASON_ACTIVE_TASK = keccak256("ACTIVE_TASK");
    bytes32 public constant REASON_UNSETTLED_REVENUE = keccak256("UNSETTLED_REVENUE");

    MachineAssetNFT public immutable machineAsset;

    SettlementController public settlementController;
    IRevenueVault public revenueVault;

    address public paymentAdapter;
    uint256 public nextOrderId = 1;

    mapping(uint256 => OrderRecord) private _orders;
    mapping(uint256 => uint256) public activeTaskCountByMachine;
    mapping(uint256 => address) public settlementBeneficiaryByOrder;
    mapping(uint256 => bool) public dividendEligibleByOrder;
    mapping(uint256 => bool) public refundAuthorizedByOrder;
    mapping(uint256 => bool) public settlementClassifiedByOrder;

    event PaymentAdapterSet(address indexed previousAdapter, address indexed newAdapter);
    event SettlementControllerSet(address indexed previousController, address indexed newController);
    event RevenueVaultSet(address indexed previousVault, address indexed newVault);
    event OrderCreated(
        uint256 indexed orderId,
        uint256 indexed machineId,
        address indexed buyer,
        uint256 grossAmount,
        address settlementBeneficiary
    );
    event OrderClassified(
        uint256 indexed orderId, bool dividendEligible, bool refundFailedOrNoValidPreviewAuthorized
    );
    event OrderPaid(uint256 indexed orderId, uint256 indexed machineId, uint256 grossAmount);
    event PreviewReady(uint256 indexed orderId, uint256 indexed machineId, bool validPreview);
    event OrderSettled(
        uint256 indexed orderId,
        uint256 indexed machineId,
        SettlementKind kind,
        uint256 refundToBuyer,
        uint256 platformShare,
        uint256 machineShare,
        bool dividendEligible
    );

    constructor(address initialOwner, address machineAssetAddress) Ownable(initialOwner) {
        machineAsset = MachineAssetNFT(machineAssetAddress);
    }

    modifier onlyPaymentAdapter() {
        if (msg.sender != paymentAdapter) {
            revert NotPaymentAdapter(msg.sender);
        }
        _;
    }

    function setPaymentAdapter(address newAdapter) external onlyOwner {
        address previousAdapter = paymentAdapter;
        paymentAdapter = newAdapter;
        emit PaymentAdapterSet(previousAdapter, newAdapter);
    }

    function setSettlementController(address controller) external onlyOwner {
        address previousController = address(settlementController);
        settlementController = SettlementController(controller);
        emit SettlementControllerSet(previousController, controller);
    }

    function setRevenueVault(address vault) external onlyOwner {
        address previousVault = address(revenueVault);
        revenueVault = IRevenueVault(vault);
        emit RevenueVaultSet(previousVault, vault);
    }

    function createOrder(uint256 machineId, uint256 grossAmount) external returns (uint256 orderId) {
        require(grossAmount > 0, "ZERO_AMOUNT");

        address machineOwner = machineAsset.ownerOf(machineId);

        orderId = nextOrderId;
        nextOrderId += 1;

        _orders[orderId] = OrderRecord({
            id: orderId,
            machineId: machineId,
            buyer: msg.sender,
            grossAmount: grossAmount,
            status: OrderStatus.Created,
            previewValid: false,
            createdAt: uint64(block.timestamp),
            paidAt: 0,
            previewReadyAt: 0,
            settledAt: 0
        });

        settlementBeneficiaryByOrder[orderId] = machineOwner;

        emit OrderCreated(orderId, machineId, msg.sender, grossAmount, machineOwner);
    }

    function markOrderPaid(uint256 orderId, bool dividendEligible, bool refundFailedOrNoValidPreviewAuthorized)
        external
        onlyPaymentAdapter
    {
        OrderRecord storage order = _orders[orderId];
        require(order.status == OrderStatus.Created, "INVALID_STATUS");

        order.status = OrderStatus.Paid;
        order.paidAt = uint64(block.timestamp);
        dividendEligibleByOrder[orderId] = dividendEligible;
        refundAuthorizedByOrder[orderId] = refundFailedOrNoValidPreviewAuthorized;
        settlementClassifiedByOrder[orderId] = true;

        activeTaskCountByMachine[order.machineId] += 1;

        emit OrderClassified(orderId, dividendEligible, refundFailedOrNoValidPreviewAuthorized);
        emit OrderPaid(orderId, order.machineId, order.grossAmount);
    }

    function markPreviewReady(uint256 orderId, bool validPreview) external {
        OrderRecord storage order = _orders[orderId];
        require(order.status == OrderStatus.Paid, "INVALID_STATUS");
        require(msg.sender == settlementBeneficiaryByOrder[orderId], "NOT_MACHINE_OWNER");

        order.status = OrderStatus.PreviewReady;
        order.previewValid = validPreview;
        order.previewReadyAt = uint64(block.timestamp);

        emit PreviewReady(orderId, order.machineId, validPreview);
    }

    function confirmResult(uint256 orderId) external {
        OrderRecord storage order = _orders[orderId];
        require(msg.sender == order.buyer, "NOT_BUYER");
        require(order.status == OrderStatus.PreviewReady, "INVALID_STATUS");
        require(order.previewValid, "PREVIEW_NOT_VALID");

        _settleOrder(order, SettlementKind.Confirmed);
    }

    function rejectValidPreview(uint256 orderId) external {
        OrderRecord storage order = _orders[orderId];
        require(msg.sender == order.buyer, "NOT_BUYER");
        require(order.status == OrderStatus.PreviewReady, "INVALID_STATUS");
        require(order.previewValid, "PREVIEW_NOT_VALID");

        _settleOrder(order, SettlementKind.RejectedValidPreview);
    }

    function refundFailedOrNoValidPreview(uint256 orderId) external {
        OrderRecord storage order = _orders[orderId];
        require(order.status == OrderStatus.Paid || order.status == OrderStatus.PreviewReady, "INVALID_STATUS");
        require(msg.sender == order.buyer || msg.sender == settlementBeneficiaryByOrder[orderId], "NOT_ALLOWED");

        if (order.status == OrderStatus.PreviewReady) {
            require(!order.previewValid, "PREVIEW_VALID");
        } else {
            require(refundAuthorizedByOrder[orderId], "REFUND_NOT_AUTHORIZED");
        }

        _settleOrder(order, SettlementKind.FailedOrNoValidPreview);
    }

    function getOrder(uint256 orderId) external view returns (OrderRecord memory) {
        return _orders[orderId];
    }

    function hasActiveTasks(uint256 machineId) external view returns (bool) {
        return activeTaskCountByMachine[machineId] > 0;
    }

    function canTransfer(uint256 machineId, address from, address to) external view returns (bool, bytes32) {
        from;
        to;

        if (activeTaskCountByMachine[machineId] > 0) {
            return (false, REASON_ACTIVE_TASK);
        }

        if (address(revenueVault) != address(0) && revenueVault.hasUnsettledRevenue(machineId)) {
            return (false, REASON_UNSETTLED_REVENUE);
        }

        return (true, bytes32(0));
    }

    function _settleOrder(OrderRecord storage order, SettlementKind kind) internal {
        require(address(settlementController) != address(0), "SETTLEMENT_NOT_SET");
        require(settlementClassifiedByOrder[order.id], "SETTLEMENT_NOT_CLASSIFIED");

        address settlementBeneficiary = settlementBeneficiaryByOrder[order.id];

        SettlementInput memory input = SettlementInput({
            orderId: order.id,
            machineId: order.machineId,
            buyer: order.buyer,
            settlementBeneficiary: settlementBeneficiary,
            grossAmount: order.grossAmount,
            dividendEligible: dividendEligibleByOrder[order.id]
        });

        SettlementBreakdown memory breakdown = settlementController.settle(input, kind);

        if (kind == SettlementKind.Confirmed) {
            order.status = OrderStatus.Confirmed;
        } else if (kind == SettlementKind.RejectedValidPreview) {
            order.status = OrderStatus.Rejected;
        } else {
            order.status = OrderStatus.Refunded;
        }

        order.settledAt = uint64(block.timestamp);
        activeTaskCountByMachine[order.machineId] -= 1;

        emit OrderSettled(
            order.id,
            order.machineId,
            kind,
            breakdown.refundToBuyer,
            breakdown.platformShare,
            breakdown.machineShare,
            breakdown.dividendEligible
        );
    }
}
