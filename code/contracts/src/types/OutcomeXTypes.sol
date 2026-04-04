// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

enum OrderStatus {
    None,
    Created,
    Paid,
    PreviewReady,
    Confirmed,
    Rejected,
    Refunded
}

enum SettlementKind {
    Confirmed,
    RejectedValidPreview,
    FailedOrNoValidPreview
}

struct OrderRecord {
    uint256 id;
    uint256 machineId;
    address buyer;
    uint256 grossAmount;
    OrderStatus status;
    bool previewValid;
    uint64 createdAt;
    uint64 paidAt;
    uint64 previewReadyAt;
    uint64 settledAt;
}

struct SettlementInput {
    uint256 orderId;
    uint256 machineId;
    address buyer;
    address settlementBeneficiary;
    uint256 grossAmount;
    bool dividendEligible;
}

struct SettlementBreakdown {
    SettlementKind kind;
    uint256 refundToBuyer;
    uint256 rejectionFee;
    uint256 platformShare;
    uint256 machineShare;
    bool dividendEligible;
}
