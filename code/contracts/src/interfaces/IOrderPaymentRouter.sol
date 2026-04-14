// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IOrderPaymentRouter {
    event PaymentFinalized(
        uint256 indexed orderId,
        uint256 indexed machineId,
        address indexed buyer,
        address payer,
        address paymentToken,
        uint256 grossAmount,
        bytes32 paymentSource,
        address settlementBeneficiary,
        bool dividendEligible,
        bool refundAuthorized
    );

    function PAYMENT_SOURCE_USDC_EIP3009() external view returns (bytes32);
    function PAYMENT_SOURCE_USDT_DIRECT() external view returns (bytes32);
    function PAYMENT_SOURCE_PWR() external view returns (bytes32);
    function PAYMENT_SOURCE_HSP() external view returns (bytes32);

    function payWithUSDCByAuthorization(
        uint256 orderId,
        uint256 amount,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;

    function payWithUSDT(uint256 orderId, uint256 amount, uint256 nonce, uint256 deadline, bytes calldata signature)
        external;

    function payWithPWR(uint256 orderId, uint256 amount) external;

    function createOrderByAdapter(address buyer, uint256 machineId, uint256 amount) external returns (uint256 orderId);

    function createOrderAndPayWithUSDC(
        uint256 machineId,
        uint256 amount,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (uint256 orderId);

    function createOrderAndPayWithUSDT(
        uint256 machineId,
        uint256 amount,
        uint256 nonce,
        uint256 deadline,
        bytes calldata signature
    ) external returns (uint256 orderId);

    function createOrderAndPayWithPWR(uint256 machineId, uint256 amount) external returns (uint256 orderId);

    function createPaidOrderByAdapter(address buyer, uint256 machineId, uint256 amount, address paymentToken)
        external
        returns (uint256 orderId);

    function payOrderByAdapter(uint256 orderId, uint256 amount, address paymentToken) external;
}
