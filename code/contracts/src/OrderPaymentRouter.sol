// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {IOrderLifecycle} from "./interfaces/IOrderLifecycle.sol";
import {IOrderPaymentRouter} from "./interfaces/IOrderPaymentRouter.sol";
import {IPermit2} from "./interfaces/IPermit2.sol";
import {OrderRecord, OrderStatus} from "./types/OutcomeXTypes.sol";

interface IERC20Like {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

interface IUSDCWithAuthorization {
    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;
}

contract OrderPaymentRouter is Ownable, IOrderPaymentRouter {
    bytes32 public constant PAYMENT_SOURCE_USDC_EIP3009 = keccak256("USDC_EIP3009");
    bytes32 public constant PAYMENT_SOURCE_USDT_PERMIT2 = keccak256("USDT_PERMIT2");
    bytes32 public constant PAYMENT_SOURCE_PWR = keccak256("PWR_DIRECT");
    bytes32 public constant PAYMENT_SOURCE_HSP = keccak256("HSP_CONFIRMED");

    IOrderLifecycle public immutable orderBook;
    IUSDCWithAuthorization public immutable usdc;
    IERC20Like public immutable usdt;
    IERC20Like public immutable pwr;
    IPermit2 public immutable permit2;
    address public settlementEscrow;

    mapping(uint256 => bytes32) public paymentSourceByOrder;

    event SettlementEscrowSet(address indexed previousEscrow, address indexed newEscrow);

    constructor(
        address initialOwner,
        address orderBookAddress,
        address usdcAddress,
        address usdtAddress,
        address pwrAddress,
        address permit2Address
    ) Ownable(initialOwner) {
        require(orderBookAddress != address(0), "ZERO_ORDER_BOOK");
        require(usdcAddress != address(0), "ZERO_USDC");
        require(usdtAddress != address(0), "ZERO_USDT");
        require(pwrAddress != address(0), "ZERO_PWR");
        require(permit2Address != address(0), "ZERO_PERMIT2");

        orderBook = IOrderLifecycle(orderBookAddress);
        usdc = IUSDCWithAuthorization(usdcAddress);
        usdt = IERC20Like(usdtAddress);
        pwr = IERC20Like(pwrAddress);
        permit2 = IPermit2(permit2Address);
    }

    function setSettlementEscrow(address newEscrow) external onlyOwner {
        require(newEscrow != address(0), "ZERO_ESCROW");
        address previousEscrow = settlementEscrow;
        settlementEscrow = newEscrow;
        emit SettlementEscrowSet(previousEscrow, newEscrow);
    }

    function payWithUSDCByAuthorization(
        uint256 orderId,
        uint256 amount,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);
        require(validAfter <= block.timestamp, "AUTH_NOT_YET_VALID");
        require(validBefore >= block.timestamp, "AUTH_EXPIRED");

        address escrowAddress = _settlementEscrow();
        usdc.receiveWithAuthorization(msg.sender, escrowAddress, amount, validAfter, validBefore, nonce, v, r, s);
        _markOrderPaid(orderId, amount, address(usdc), PAYMENT_SOURCE_USDC_EIP3009, dividendEligible, msg.sender);
    }

    function payWithUSDT(uint256 orderId, uint256 amount, uint256 nonce, uint256 deadline, bytes calldata signature)
        external
    {
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);
        require(deadline >= block.timestamp, "PERMIT_EXPIRED");

        IPermit2.PermitTransferFrom memory permit = IPermit2.PermitTransferFrom({
            permitted: IPermit2.TokenPermissions({token: address(usdt), amount: amount}),
            nonce: nonce,
            deadline: deadline
        });
        IPermit2.SignatureTransferDetails memory transferDetails =
            IPermit2.SignatureTransferDetails({to: _settlementEscrow(), requestedAmount: amount});

        permit2.permitTransferFrom(permit, transferDetails, msg.sender, signature);
        _markOrderPaid(orderId, amount, address(usdt), PAYMENT_SOURCE_USDT_PERMIT2, dividendEligible, msg.sender);
    }

    function payWithPWR(uint256 orderId, uint256 amount) external {
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);

        bool success = pwr.transferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "PWR_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, address(pwr), PAYMENT_SOURCE_PWR, dividendEligible, msg.sender);
    }

    function createOrderAndPayWithUSDC(
        uint256 machineId,
        uint256 amount,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external returns (uint256 orderId) {
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, amount);
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);
        require(validAfter <= block.timestamp, "AUTH_NOT_YET_VALID");
        require(validBefore >= block.timestamp, "AUTH_EXPIRED");

        address escrowAddress = _settlementEscrow();
        usdc.receiveWithAuthorization(msg.sender, escrowAddress, amount, validAfter, validBefore, nonce, v, r, s);
        _markOrderPaid(orderId, amount, address(usdc), PAYMENT_SOURCE_USDC_EIP3009, dividendEligible, msg.sender);
    }

    function createOrderAndPayWithUSDT(
        uint256 machineId,
        uint256 amount,
        uint256 nonce,
        uint256 deadline,
        bytes calldata signature
    ) external returns (uint256 orderId) {
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, amount);
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);
        require(deadline >= block.timestamp, "PERMIT_EXPIRED");

        IPermit2.PermitTransferFrom memory permit = IPermit2.PermitTransferFrom({
            permitted: IPermit2.TokenPermissions({token: address(usdt), amount: amount}),
            nonce: nonce,
            deadline: deadline
        });
        IPermit2.SignatureTransferDetails memory transferDetails =
            IPermit2.SignatureTransferDetails({to: _settlementEscrow(), requestedAmount: amount});

        permit2.permitTransferFrom(permit, transferDetails, msg.sender, signature);
        _markOrderPaid(orderId, amount, address(usdt), PAYMENT_SOURCE_USDT_PERMIT2, dividendEligible, msg.sender);
    }

    function createOrderAndPayWithPWR(uint256 machineId, uint256 amount) external returns (uint256 orderId) {
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, amount);
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender);

        bool success = pwr.transferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "PWR_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, address(pwr), PAYMENT_SOURCE_PWR, dividendEligible, msg.sender);
    }

    function createPaidOrderByAdapter(address buyer, uint256 machineId, uint256 amount, address paymentToken)
        external
        onlyOwner
        returns (uint256 orderId)
    {
        require(buyer != address(0), "ZERO_BUYER");
        require(paymentToken != address(0), "ZERO_TOKEN");

        bool success = IERC20Like(paymentToken).transferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "ADAPTER_TRANSFER_FAILED");

        orderId = orderBook.createOrderForBuyer(buyer, machineId, amount);
        bool dividendEligible = _validateOrderForPayment(orderId, amount, buyer);
        _markOrderPaid(orderId, amount, paymentToken, PAYMENT_SOURCE_HSP, dividendEligible, msg.sender);
    }

    function _validateOrderForPayment(uint256 orderId, uint256 amount, address expectedBuyer)
        internal
        view
        returns (bool dividendEligible)
    {
        OrderRecord memory order = orderBook.getOrder(orderId);
        require(order.status == OrderStatus.Created, "INVALID_STATUS");
        require(order.buyer == expectedBuyer, "NOT_BUYER");
        require(order.grossAmount == amount, "INVALID_AMOUNT");
        require(paymentSourceByOrder[orderId] == bytes32(0), "ALREADY_PAID");

        address settlementBeneficiary = orderBook.settlementBeneficiaryByOrder(orderId);
        dividendEligible = settlementBeneficiary != expectedBuyer;
    }

    function _markOrderPaid(
        uint256 orderId,
        uint256 amount,
        address token,
        bytes32 paymentSource,
        bool dividendEligible,
        address payer
    ) internal {
        OrderRecord memory order = orderBook.getOrder(orderId);
        address settlementBeneficiary = orderBook.settlementBeneficiaryByOrder(orderId);
        paymentSourceByOrder[orderId] = paymentSource;
        orderBook.markOrderPaid(orderId, dividendEligible, true, token);
        emit PaymentFinalized(
            orderId,
            order.machineId,
            order.buyer,
            payer,
            token,
            amount,
            paymentSource,
            settlementBeneficiary,
            dividendEligible,
            true
        );
    }

    function _settlementEscrow() internal view returns (address escrow) {
        escrow = settlementEscrow;
        require(escrow != address(0), "SETTLEMENT_NOT_SET");
    }
}
