// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT} from "../src/MachineAssetNFT.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {OrderPaymentRouter} from "../src/OrderPaymentRouter.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {MockUSDCWithAuthorization} from "../src/mocks/MockUSDCWithAuthorization.sol";
import {MockUSDT} from "../src/mocks/MockUSDT.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract OrderPaymentRouterTest is TestBase {
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
    event RefundClaimedDetailed(
        address indexed buyer, address indexed token, uint256 amount, uint256 remainingRefundableAfter
    );
    event PlatformRevenueClaimedDetailed(
        address indexed treasury, address indexed token, uint256 amount, uint256 remainingPlatformAccruedAfter
    );

    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);

    uint256 internal constant PWR_ANCHOR_PRICE_CENTS = 25;

    MockUSDCWithAuthorization internal usdc;
    MockUSDT internal usdt;
    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;
    OrderPaymentRouter internal router;

    uint256 internal machineId;

    function _pwrWeiForCents(uint256 amountCents) internal pure returns (uint256) {
        return (amountCents * 1 ether) / PWR_ANCHOR_PRICE_CENTS;
    }

    function _stablecoinUnitsForCents(uint256 amountCents) internal pure returns (uint256) {
        return amountCents * 10_000;
    }

    function setUp() public {
        usdc = new MockUSDCWithAuthorization();
        usdt = new MockUSDT();
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset), 25);
        settlement = new SettlementController(ADMIN, address(revenueVault), address(pwr), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));
        router = new OrderPaymentRouter(ADMIN, address(orderBook), address(usdc), address(usdt), address(pwr));

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        usdc.mint(ADMIN, 5_000_000);
        usdt.mint(ADMIN, 5_000_000);
        usdc.mint(BUYER, 5_000_000);
        usdt.mint(BUYER, 5_000_000);
        revenueVault.setSettlementController(address(settlement));
        router.setSettlementEscrow(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(address(router));
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        vm.stopPrank();
    }

    function testUSDCFailedBeforePreviewCanRefundRealFunds() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 100);

        vm.prank(BUYER);
        router.payWithUSDCByAuthorization(
            orderId,
            _stablecoinUnitsForCents(100),
            block.timestamp - 1,
            block.timestamp + 1 days,
            keccak256("nonce-1"),
            0,
            bytes32(0),
            bytes32(0)
        );

        assertEq(usdc.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "settlement should escrow usdc");

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Refunded), "expected refunded status");
        assertEq(settlement.refundableByToken(BUYER, address(usdc)), _stablecoinUnitsForCents(100), "refund ledger mismatch");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit RefundClaimedDetailed(BUYER, address(usdc), _stablecoinUnitsForCents(100), 0);
        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(usdc));
        assertEq(claimed, _stablecoinUnitsForCents(100), "refund amount mismatch");
        assertEq(usdc.balanceOf(BUYER), 5_000_000, "buyer should recover all usdc");
        assertEq(usdc.balanceOf(address(settlement)), 0, "settlement escrow should be empty");
    }

    function testCreateAndPayWithUSDCRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithUSDC(
            machineId,
            _stablecoinUnitsForCents(100),
            block.timestamp - 1,
            block.timestamp + 1 days,
            keccak256("nonce-create-pay-1"),
            0,
            bytes32(0),
            bytes32(0)
        );

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdc.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "settlement should escrow usdc");
    }

    function testCreateAndPayWithUSDTRecordsBuyerAsCaller() public {
        vm.prank(BUYER);
        usdt.approve(address(router), _stablecoinUnitsForCents(100));

        vm.prank(BUYER);
        uint256 orderId =
            router.createOrderAndPayWithUSDT(machineId, _stablecoinUnitsForCents(100), 1, block.timestamp + 1 days, hex"BEEF");

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(usdt.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "settlement should escrow usdt");
    }

    function testCreateAndPayWithPWRRecordsBuyerAsCaller() public {
        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.prank(BUYER);
        uint256 orderId = router.createOrderAndPayWithPWR(machineId, _pwrWeiForCents(1000));

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should be external caller");
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(pwr.balanceOf(address(settlement)), _pwrWeiForCents(1000), "settlement should escrow pwr");
    }

    function testCreateOrderByAdapterCreatesUnpaidOrderForBuyer() public {
        vm.prank(ADMIN);
        uint256 orderId = router.createOrderByAdapter(BUYER, machineId, 100);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(order.buyer, BUYER, "buyer should match adapter input");
        assertEq(uint256(order.status), uint256(OrderStatus.Created), "order should stay unpaid");
        assertEq(orderBook.activeTaskCountByMachine(machineId), 0, "unpaid order should not lock active task");
    }

    function testCreatePaidOrderByAdapterIsDisabled() public {
        vm.startPrank(ADMIN);
        usdc.approve(address(router), 1_000_000);
        vm.expectRevert(bytes("LEGACY_ROUTE_DISABLED"));
        router.createPaidOrderByAdapter(BUYER, machineId, 1_000_000, address(usdc));
        vm.stopPrank();
    }

    function testPayOrderByAdapterPaysExistingOrderAndBlocksTransfer() public {
        vm.prank(ADMIN);
        uint256 orderId = router.createOrderByAdapter(BUYER, machineId, 100);

        uint256 adminBalanceBefore = usdc.balanceOf(ADMIN);
        vm.startPrank(ADMIN);
        usdc.approve(address(router), _stablecoinUnitsForCents(100));
        vm.expectEmit(true, true, true, true, address(router));
        emit PaymentFinalized(
            orderId,
            machineId,
            BUYER,
            ADMIN,
            address(usdc),
            _stablecoinUnitsForCents(100),
            router.PAYMENT_SOURCE_HSP(),
            MACHINE_OWNER,
            true,
            true
        );
        router.payOrderByAdapter(orderId, _stablecoinUnitsForCents(100), address(usdc));
        vm.stopPrank();

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Paid), "order should be paid");
        assertEq(orderBook.activeTaskCountByMachine(machineId), 1, "active task should be tracked");
        assertEq(usdc.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "settlement should escrow adapter funds");
        assertEq(usdc.balanceOf(ADMIN), adminBalanceBefore - _stablecoinUnitsForCents(100), "adapter caller should fund escrow");
    }

    function testPayWithPWRRejectsAmountDifferentFromFrozenOrderGross() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, _pwrWeiForCents(1000));

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.prank(BUYER);
        vm.expectRevert(bytes("INVALID_AMOUNT"));
        router.payWithPWR(orderId, _pwrWeiForCents(900));
    }

    function testUSDTConfirmedOrderCreatesRealPlatformClaimAndReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 100);

        vm.prank(BUYER);
        usdt.approve(address(router), _stablecoinUnitsForCents(100));

        vm.prank(BUYER);
        router.payWithUSDT(orderId, _stablecoinUnitsForCents(100), 1, block.timestamp + 1 days, hex"BEEF");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(usdt)), _stablecoinUnitsForCents(10), "platform usdt claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), _pwrWeiForCents(90), "machine pwr claim mismatch");
        assertEq(usdt.balanceOf(address(settlement)), _stablecoinUnitsForCents(100), "escrow should hold full reserve before claims");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit PlatformRevenueClaimedDetailed(PLATFORM_TREASURY, address(usdt), _stablecoinUnitsForCents(10), 0);
        vm.prank(PLATFORM_TREASURY);
        uint256 claimed = settlement.claimPlatformRevenue(address(usdt));
        assertEq(claimed, _stablecoinUnitsForCents(10), "platform claim amount mismatch");
        assertEq(usdt.balanceOf(PLATFORM_TREASURY), _stablecoinUnitsForCents(10), "platform should receive real usdt");
        assertEq(usdt.balanceOf(address(settlement)), _stablecoinUnitsForCents(90), "remaining usdt should stay as reserve backing");
    }

    function testPWRConfirmedOrderCreatesPlatformClaimAndMachineReserve() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, _pwrWeiForCents(1000));

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.prank(BUYER);
        router.payWithPWR(orderId, _pwrWeiForCents(1000));

        assertEq(pwr.balanceOf(address(settlement)), _pwrWeiForCents(1000), "settlement should escrow pwr");

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedByToken(address(pwr)), _pwrWeiForCents(100), "platform pwr claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), _pwrWeiForCents(900), "machine pwr accrual mismatch");
        assertEq(pwr.balanceOf(address(settlement)), _pwrWeiForCents(100), "settlement should retain only platform share");
        assertEq(pwr.balanceOf(address(revenueVault)), _pwrWeiForCents(900), "revenue vault should hold funded machine share");

        vm.prank(MACHINE_OWNER);
        uint256 machineClaimed = revenueVault.claim(machineId);
        assertEq(machineClaimed, _pwrWeiForCents(900), "machine claim amount mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), _pwrWeiForCents(900), "machine owner should receive funded pwr");
        assertEq(pwr.balanceOf(address(revenueVault)), 0, "revenue vault should be empty after machine claim");

        vm.prank(PLATFORM_TREASURY);
        uint256 platformClaimed = settlement.claimPlatformRevenue(address(pwr));
        assertEq(platformClaimed, _pwrWeiForCents(100), "platform claim amount mismatch");
        assertEq(pwr.balanceOf(PLATFORM_TREASURY), _pwrWeiForCents(100), "treasury should receive funded pwr");
        assertEq(pwr.balanceOf(address(settlement)), 0, "settlement should fully drain after claims");
    }

    function testPWRRejectedValidPreviewFullyDistributesEscrowedFunds() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, _pwrWeiForCents(1000));

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.prank(BUYER);
        router.payWithPWR(orderId, _pwrWeiForCents(1000));

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.rejectValidPreview(orderId);

        assertEq(settlement.refundableByToken(BUYER, address(pwr)), _pwrWeiForCents(700), "buyer refund ledger mismatch");
        assertEq(settlement.platformAccruedByToken(address(pwr)), _pwrWeiForCents(30), "platform pwr claim mismatch");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), _pwrWeiForCents(270), "machine pwr accrual mismatch");
        assertEq(pwr.balanceOf(address(settlement)), _pwrWeiForCents(730), "settlement should retain refund plus platform share");
        assertEq(pwr.balanceOf(address(revenueVault)), _pwrWeiForCents(270), "revenue vault should hold machine share");

        vm.prank(BUYER);
        uint256 refunded = settlement.claimRefund(address(pwr));
        assertEq(refunded, _pwrWeiForCents(700), "refund amount mismatch");
        assertEq(pwr.balanceOf(BUYER), _pwrWeiForCents(700), "buyer should receive funded pwr refund");
        assertEq(pwr.balanceOf(address(settlement)), _pwrWeiForCents(30), "settlement should retain only platform share after refund");

        vm.prank(MACHINE_OWNER);
        uint256 machineClaimed = revenueVault.claim(machineId);
        assertEq(machineClaimed, _pwrWeiForCents(270), "machine claim mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), _pwrWeiForCents(270), "machine owner should receive funded pwr");
        assertEq(pwr.balanceOf(address(revenueVault)), 0, "revenue vault should be empty after machine claim");

        vm.prank(PLATFORM_TREASURY);
        uint256 platformClaimed = settlement.claimPlatformRevenue(address(pwr));
        assertEq(platformClaimed, _pwrWeiForCents(30), "platform claim mismatch");
        assertEq(pwr.balanceOf(PLATFORM_TREASURY), _pwrWeiForCents(30), "treasury should receive funded pwr");
        assertEq(pwr.balanceOf(address(settlement)), 0, "settlement should fully drain after all pwr claims");
    }

    function testPWRFailedBeforePreviewCanRefundPaidPWR() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, _pwrWeiForCents(1000));

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.prank(BUYER);
        router.payWithPWR(orderId, _pwrWeiForCents(1000));

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        assertEq(settlement.refundableByToken(BUYER, address(pwr)), _pwrWeiForCents(1000), "refund ledger mismatch");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit RefundClaimedDetailed(BUYER, address(pwr), _pwrWeiForCents(1000), 0);
        vm.prank(BUYER);
        uint256 claimed = settlement.claimRefund(address(pwr));
        assertEq(claimed, _pwrWeiForCents(1000), "refund amount mismatch");
        assertEq(pwr.balanceOf(BUYER), _pwrWeiForCents(1000), "buyer should recover full pwr refund");
        assertEq(pwr.balanceOf(address(settlement)), 0, "settlement should release the full anchored payment");
    }

    function testPaymentRouterRejectsExpiredUnpaidOrder() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, _pwrWeiForCents(1000));

        vm.startPrank(ADMIN);
        pwr.setMinter(ADMIN, true);
        pwr.mint(BUYER, _pwrWeiForCents(1000));
        vm.stopPrank();

        vm.prank(BUYER);
        pwr.approve(address(router), _pwrWeiForCents(1000));

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL() + 1);
        vm.prank(BUYER);
        vm.expectRevert(bytes("ORDER_EXPIRED"));
        router.payWithPWR(orderId, _pwrWeiForCents(1000));
    }
}
