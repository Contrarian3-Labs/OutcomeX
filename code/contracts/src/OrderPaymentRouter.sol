// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "./common/Ownable.sol";
import {SafeERC20Like} from "./common/SafeERC20Like.sol";
import {IOrderLifecycle} from "./interfaces/IOrderLifecycle.sol";
import {IOrderPaymentRouter} from "./interfaces/IOrderPaymentRouter.sol";
import {OrderRecord, OrderStatus} from "./types/OutcomeXTypes.sol";

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
    using SafeERC20Like for address;
    uint256 internal constant STABLECOIN_UNITS_PER_CENT = 10_000;

    bytes32 public constant PAYMENT_SOURCE_USDC_EIP3009 = keccak256("USDC_EIP3009");
    bytes32 public constant PAYMENT_SOURCE_USDT_DIRECT = keccak256("USDT_DIRECT");
    bytes32 public constant PAYMENT_SOURCE_PWR = keccak256("PWR_DIRECT");
    bytes32 public constant PAYMENT_SOURCE_HSP = keccak256("HSP_CONFIRMED");

    IOrderLifecycle public immutable orderBook;
    IUSDCWithAuthorization public immutable usdc;
    address public immutable usdt;
    address public immutable pwr;
    address public settlementEscrow;

    mapping(uint256 => bytes32) public paymentSourceByOrder;

    event SettlementEscrowSet(address indexed previousEscrow, address indexed newEscrow);

    constructor(
        address initialOwner,
        address orderBookAddress,
        address usdcAddress,
        address usdtAddress,
        address pwrAddress
    ) Ownable(initialOwner) {
        require(orderBookAddress != address(0), "ZERO_ORDER_BOOK");
        require(usdcAddress != address(0), "ZERO_USDC");
        require(usdtAddress != address(0), "ZERO_USDT");
        require(pwrAddress != address(0), "ZERO_PWR");

        orderBook = IOrderLifecycle(orderBookAddress);
        usdc = IUSDCWithAuthorization(usdcAddress);
        usdt = usdtAddress;
        pwr = pwrAddress;
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
        uint256 grossAmount = _stablecoinUnitsToCents(amount);
        bool dividendEligible = _validateOrderForPayment(orderId, grossAmount, msg.sender, true);
        require(validAfter <= block.timestamp, "AUTH_NOT_YET_VALID");
        require(validBefore >= block.timestamp, "AUTH_EXPIRED");

        address escrowAddress = _settlementEscrow();
        usdc.receiveWithAuthorization(msg.sender, escrowAddress, amount, validAfter, validBefore, nonce, v, r, s);
        _markOrderPaid(orderId, amount, address(usdc), PAYMENT_SOURCE_USDC_EIP3009, dividendEligible, msg.sender);
    }

    function payWithUSDT(uint256 orderId, uint256 amount, uint256, uint256, bytes calldata) external {
        uint256 grossAmount = _stablecoinUnitsToCents(amount);
        bool dividendEligible = _validateOrderForPayment(orderId, grossAmount, msg.sender, true);

        bool success = address(usdt).safeTransferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "USDT_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, usdt, PAYMENT_SOURCE_USDT_DIRECT, dividendEligible, msg.sender);
    }

    function payWithPWR(uint256 orderId, uint256 amount) external {
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender, true);

        bool success = pwr.safeTransferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "PWR_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, pwr, PAYMENT_SOURCE_PWR, dividendEligible, msg.sender);
    }

    function createOrderByAdapter(address buyer, uint256 machineId, uint256 amount)
        external
        onlyOwner
        returns (uint256 orderId)
    {
        require(buyer != address(0), "ZERO_BUYER");
        require(amount > 0, "ZERO_AMOUNT");
        orderId = orderBook.createOrderForBuyer(buyer, machineId, amount);
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
        uint256 grossAmount = _stablecoinUnitsToCents(amount);
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, grossAmount);
        bool dividendEligible = _validateOrderForPayment(orderId, grossAmount, msg.sender, true);
        require(validAfter <= block.timestamp, "AUTH_NOT_YET_VALID");
        require(validBefore >= block.timestamp, "AUTH_EXPIRED");

        address escrowAddress = _settlementEscrow();
        usdc.receiveWithAuthorization(msg.sender, escrowAddress, amount, validAfter, validBefore, nonce, v, r, s);
        _markOrderPaid(orderId, amount, address(usdc), PAYMENT_SOURCE_USDC_EIP3009, dividendEligible, msg.sender);
    }

    function createOrderAndPayWithUSDT(
        uint256 machineId,
        uint256 amount,
        uint256,
        uint256,
        bytes calldata
    ) external returns (uint256 orderId) {
        uint256 grossAmount = _stablecoinUnitsToCents(amount);
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, grossAmount);
        bool dividendEligible = _validateOrderForPayment(orderId, grossAmount, msg.sender, true);

        bool success = address(usdt).safeTransferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "USDT_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, usdt, PAYMENT_SOURCE_USDT_DIRECT, dividendEligible, msg.sender);
    }

    function createOrderAndPayWithPWR(uint256 machineId, uint256 amount) external returns (uint256 orderId) {
        orderId = orderBook.createOrderForBuyer(msg.sender, machineId, amount);
        bool dividendEligible = _validateOrderForPayment(orderId, amount, msg.sender, true);

        bool success = pwr.safeTransferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "PWR_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, pwr, PAYMENT_SOURCE_PWR, dividendEligible, msg.sender);
    }

    function createPaidOrderByAdapter(address buyer, uint256 machineId, uint256 amount, address paymentToken)
        external
        onlyOwner
        returns (uint256 orderId)
    {
        buyer;
        machineId;
        amount;
        paymentToken;
        orderId = 0;
        revert("LEGACY_ROUTE_DISABLED");
    }

    function payOrderByAdapter(uint256 orderId, uint256 amount, address paymentToken) external onlyOwner {
        require(paymentToken != address(0), "ZERO_TOKEN");
        require(_isSupportedHSPToken(paymentToken), "UNSUPPORTED_HSP_TOKEN");

        OrderRecord memory order = orderBook.getOrder(orderId);
        uint256 grossAmount = _stablecoinUnitsToCents(amount);
        bool dividendEligible = _validateOrderForPayment(orderId, grossAmount, order.buyer, true);

        bool success = paymentToken.safeTransferFrom(msg.sender, _settlementEscrow(), amount);
        require(success, "ADAPTER_TRANSFER_FAILED");

        _markOrderPaid(orderId, amount, paymentToken, PAYMENT_SOURCE_HSP, dividendEligible, msg.sender);
    }

    function _validateOrderForPayment(uint256 orderId, uint256 amount, address expectedBuyer, bool enforceGrossAmountMatch)
        internal
        view
        returns (bool dividendEligible)
    {
        OrderRecord memory order = orderBook.getOrder(orderId);
        require(order.status == OrderStatus.Created, "INVALID_STATUS");
        require(order.buyer == expectedBuyer, "NOT_BUYER");
        require(amount > 0, "ZERO_AMOUNT");
        if (enforceGrossAmountMatch) {
            require(order.grossAmount == amount, "INVALID_AMOUNT");
        }
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
        orderBook.markOrderPaid(orderId, dividendEligible, true, token, amount);
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

    function _isSupportedHSPToken(address token) internal view returns (bool) {
        return token == address(usdc) || token == usdt;
    }

    function _stablecoinUnitsToCents(uint256 amount) internal pure returns (uint256) {
        require(amount > 0, "ZERO_AMOUNT");
        require(amount % STABLECOIN_UNITS_PER_CENT == 0, "INVALID_AMOUNT_SCALE");
        return amount / STABLECOIN_UNITS_PER_CENT;
    }
}
