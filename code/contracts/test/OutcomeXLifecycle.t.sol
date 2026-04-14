// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {MachineAssetNFT, TransferGuardBlocked} from "../src/MachineAssetNFT.sol";
import {OrderBook} from "../src/OrderBook.sol";
import {PWRToken} from "../src/PWRToken.sol";
import {RevenueVault} from "../src/RevenueVault.sol";
import {SettlementController} from "../src/SettlementController.sol";
import {OrderRecord, OrderStatus} from "../src/types/OutcomeXTypes.sol";
import {TestBase} from "./utils/TestBase.sol";

contract OutcomeXLifecycleTest is TestBase {
    event RefundClaimedDetailed(
        address indexed buyer, address indexed token, uint256 amount, uint256 remainingRefundableAfter
    );
    event PlatformRevenueClaimedDetailed(
        address indexed treasury, address indexed token, uint256 amount, uint256 remainingPlatformAccruedAfter
    );
    event MachineRevenueClaimedDetailed(
        uint256 indexed machineId,
        address indexed machineOwner,
        uint256 amount,
        uint256 remainingClaimableForMachineOwnerAfter,
        uint256 remainingUnsettledRevenueByMachineAfter
    );

    address internal constant ADMIN = address(0xA11CE);
    address internal constant PLATFORM_TREASURY = address(0xBEEF);
    address internal constant PAYMENT_ADAPTER = address(0xAD7E2);
    address internal constant MACHINE_OWNER = address(0xCAFE);
    address internal constant BUYER = address(0xB0B);
    address internal constant BUYER_TWO = address(0xB0C);
    address internal constant RECEIVER = address(0xD00D);
    uint256 internal constant PWR_ANCHOR_PRICE_CENTS = 25;

    PWRToken internal pwr;
    MachineAssetNFT internal machineAsset;
    RevenueVault internal revenueVault;
    SettlementController internal settlement;
    OrderBook internal orderBook;

    uint256 internal machineId;

    function _pwrWeiForCents(uint256 amountCents) internal pure returns (uint256) {
        return (amountCents * 1 ether) / PWR_ANCHOR_PRICE_CENTS;
    }

    function setUp() public {
        pwr = new PWRToken(ADMIN);
        machineAsset = new MachineAssetNFT(ADMIN);
        revenueVault = new RevenueVault(ADMIN, address(pwr), address(machineAsset), 25);
        settlement = new SettlementController(ADMIN, address(revenueVault), address(pwr), PLATFORM_TREASURY);
        orderBook = new OrderBook(ADMIN, address(machineAsset));

        vm.startPrank(ADMIN);
        pwr.setMinter(address(revenueVault), true);
        revenueVault.setSettlementController(address(settlement));
        settlement.setOrderBook(address(orderBook));
        orderBook.setSettlementController(address(settlement));
        orderBook.setRevenueVault(address(revenueVault));
        orderBook.setPaymentAdapter(PAYMENT_ADAPTER);
        machineAsset.setTransferGuard(address(orderBook));
        machineId = machineAsset.mintMachine(MACHINE_OWNER, "ipfs://machine-001");
        vm.stopPrank();
    }

    function testConfirmLifecycleAndClaimFlow() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false, address(0), 1_000);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Confirmed), "expected confirmed status");
        assertEq(settlement.platformAccruedUSDT(), 100, "platform should receive 10%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), _pwrWeiForCents(900), "machine side should receive anchored PWR");

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(
            abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_UNSETTLED_REVENUE())
        );
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        vm.stopPrank();

        vm.expectEmit(true, true, false, true, address(revenueVault));
        emit MachineRevenueClaimedDetailed(machineId, MACHINE_OWNER, _pwrWeiForCents(900), 0, 0);
        vm.prank(MACHINE_OWNER);
        uint256 claimed = revenueVault.claim(machineId);
        assertEq(claimed, _pwrWeiForCents(900), "machine owner claim mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), _pwrWeiForCents(900), "PWR balance mismatch");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "ownership should transfer after claim");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit PlatformRevenueClaimedDetailed(PLATFORM_TREASURY, address(0), 100, 0);
        vm.prank(PLATFORM_TREASURY);
        uint256 platformClaimed = settlement.claimPlatformRevenue();
        assertEq(platformClaimed, 100, "platform claim mismatch");
    }

    function testRejectValidPreviewEconomics() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false, address(0), 1_000);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.rejectValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Rejected), "expected rejected status");
        assertEq(settlement.refundableUSDT(BUYER), 700, "buyer refund should be 70%");
        assertEq(settlement.platformAccruedUSDT(), 30, "platform share should be 3%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), _pwrWeiForCents(270), "machine share should be 27% in anchored PWR");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit RefundClaimedDetailed(BUYER, address(0), 700, 0);
        vm.prank(BUYER);
        uint256 buyerClaimed = settlement.claimRefund();
        assertEq(buyerClaimed, 700, "refund claim mismatch");

        vm.expectEmit(true, true, false, true, address(revenueVault));
        emit MachineRevenueClaimedDetailed(machineId, MACHINE_OWNER, _pwrWeiForCents(270), 0, 0);
        vm.prank(MACHINE_OWNER);
        uint256 machineClaimed = revenueVault.claim(machineId);
        assertEq(machineClaimed, _pwrWeiForCents(270), "machine claim mismatch");
        assertEq(pwr.balanceOf(MACHINE_OWNER), _pwrWeiForCents(270), "machine PWR mismatch");
    }

    function testRefundWhenNoValidPreviewAndActiveTaskGuard() public {
        vm.prank(BUYER_TWO);
        uint256 orderId = orderBook.createOrder(machineId, 500);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, true, address(0), 500);

        vm.startPrank(MACHINE_OWNER);
        vm.expectRevert(abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_ACTIVE_TASK()));
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        vm.stopPrank();

        vm.prank(BUYER_TWO);
        orderBook.refundFailedOrNoValidPreview(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Refunded), "expected refunded status");
        assertEq(settlement.refundableUSDT(BUYER_TWO), 500, "buyer should receive full refund");
        assertEq(settlement.platformAccruedUSDT(), 0, "no platform fee on failed preview");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "no machine accrual on failed preview");

        vm.expectEmit(true, true, false, true, address(settlement));
        emit RefundClaimedDetailed(BUYER_TWO, address(0), 500, 0);
        vm.prank(BUYER_TWO);
        uint256 buyerClaimed = settlement.claimRefund();
        assertEq(buyerClaimed, 500, "refund claim mismatch");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "transfer should be allowed after settlement");
    }

    function testSettlementClassificationControlsDividendEligibility() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, false, false, address(0), 1_000);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(settlement.platformAccruedUSDT(), 100, "platform should still receive 10%");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "non-eligible order should not accrue dividends");
        assertEq(revenueVault.nonDividendRevenueByMachine(machineId), 900, "non-eligible order should track non-dividend value");

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "non-eligible order should not block transfer");
    }

    function testRefundAfterInvalidPreviewMarked() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 600);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false, address(0), 600);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, false);

        vm.prank(BUYER);
        orderBook.refundFailedOrNoValidPreview(orderId);

        assertEq(settlement.refundableUSDT(BUYER), 600, "full refund expected");
        assertEq(revenueVault.unsettledRevenueByMachine(machineId), 0, "no machine payout expected");
        assertEq(settlement.platformAccruedUSDT(), 0, "no platform fee expected");
    }

    function testRefundPaidOrderRequiresFailureBasis() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 400);

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false, address(0), 400);

        vm.prank(BUYER);
        vm.expectRevert(bytes("REFUND_NOT_AUTHORIZED"));
        orderBook.refundFailedOrNoValidPreview(orderId);
    }

    function testSettlementUsesSnapshotBeneficiaryAfterOwnershipTransfer() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 1_000);

        vm.prank(MACHINE_OWNER);
        machineAsset.transferFrom(MACHINE_OWNER, RECEIVER, machineId);
        assertEq(machineAsset.ownerOf(machineId), RECEIVER, "ownership should move before payment");

        vm.prank(PAYMENT_ADAPTER);
        orderBook.markOrderPaid(orderId, true, false, address(0), 1_000);

        vm.prank(RECEIVER);
        vm.expectRevert(bytes("NOT_MACHINE_OWNER"));
        orderBook.markPreviewReady(orderId, true);

        vm.prank(MACHINE_OWNER);
        orderBook.markPreviewReady(orderId, true);

        vm.prank(BUYER);
        orderBook.confirmResult(orderId);

        assertEq(revenueVault.claimableByMachineOwner(machineId, MACHINE_OWNER), _pwrWeiForCents(900), "snapshot owner should accrue anchored claim");
        assertEq(revenueVault.claimableByMachineOwner(machineId, RECEIVER), 0, "new owner should not accrue old order");

        vm.startPrank(RECEIVER);
        vm.expectRevert(
            abi.encodeWithSelector(TransferGuardBlocked.selector, machineId, orderBook.REASON_UNSETTLED_REVENUE())
        );
        machineAsset.transferFrom(RECEIVER, BUYER_TWO, machineId);
        vm.stopPrank();

        vm.prank(MACHINE_OWNER);
        uint256 claimed = revenueVault.claim(machineId);
        assertEq(claimed, _pwrWeiForCents(900), "snapshot owner claim mismatch");

        vm.prank(RECEIVER);
        machineAsset.transferFrom(RECEIVER, BUYER_TWO, machineId);
        assertEq(machineAsset.ownerOf(machineId), BUYER_TWO, "transfer should unlock after claim");
    }

    function testBuyerCanCancelUnpaidOrderBeforeExpiry() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 800);

        vm.prank(BUYER);
        orderBook.cancelUnpaidOrder(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Cancelled), "expected cancelled status");
        assertGt(order.cancelledAt, 0, "cancelledAt should be set");
        assertTrue(!order.cancelledAsExpired, "buyer cancel should not be marked expired");
    }

    function testAnyoneCanExpireUnpaidOrderAfterDeadline() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 800);

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL() + 1);
        orderBook.expireUnpaidOrder(orderId);

        OrderRecord memory order = orderBook.getOrder(orderId);
        assertEq(uint256(order.status), uint256(OrderStatus.Cancelled), "expected cancelled status");
        assertEq(order.cancelledAt, uint64(block.timestamp), "cancel timestamp mismatch");
        assertTrue(order.cancelledAsExpired, "expired cleanup should persist expiry truth");
    }

    function testExpireUnpaidOrderRejectsNonexistentOrderId() public {
        vm.expectRevert(bytes("ORDER_NOT_FOUND"));
        orderBook.expireUnpaidOrder(999);
    }

    function testExpireUnpaidOrderRevertsBeforeDeadline() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 800);

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL() - 1);
        vm.expectRevert(bytes("ORDER_NOT_EXPIRED"));
        orderBook.expireUnpaidOrder(orderId);
    }

    function testExpireUnpaidOrderRevertsAtExactDeadline() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 800);

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL());
        vm.expectRevert(bytes("ORDER_NOT_EXPIRED"));
        orderBook.expireUnpaidOrder(orderId);
    }

    function testBuyerCannotCancelUnpaidOrderAfterExpiry() public {
        vm.prank(BUYER);
        uint256 orderId = orderBook.createOrder(machineId, 800);

        vm.warp(block.timestamp + orderBook.UNPAID_ORDER_TTL() + 1);
        vm.prank(BUYER);
        vm.expectRevert(bytes("ORDER_EXPIRED"));
        orderBook.cancelUnpaidOrder(orderId);
    }
}
