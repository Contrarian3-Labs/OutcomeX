// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IOrderPaymentRouter {
    event OrderPaymentReceived(
        uint256 indexed orderId, address indexed payer, address indexed token, uint256 amount, bytes32 paymentSource
    );

    function PAYMENT_SOURCE_USDC_EIP3009() external view returns (bytes32);
    function PAYMENT_SOURCE_USDT_PERMIT2() external view returns (bytes32);
    function PAYMENT_SOURCE_PWR() external view returns (bytes32);

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
}
