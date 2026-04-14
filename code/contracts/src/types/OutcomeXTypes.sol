// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

enum OrderStatus {
    None,
    Created,
    Paid,
    PreviewReady,
    Confirmed,
    Rejected,
    Refunded,
    Cancelled
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
    bool cancelledAsExpired;
    uint64 createdAt;
    uint64 paidAt;
    uint64 previewReadyAt;
    uint64 settledAt;
    uint64 cancelledAt;
}

struct SettlementInput {
    uint256 orderId;
    uint256 machineId;
    address buyer;
    address settlementBeneficiary;
    address paymentToken;
    uint256 paymentAmount;
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
